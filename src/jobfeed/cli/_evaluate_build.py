"""Composition root for the evaluate command: build + run EvaluateService.

Lives apart from cli/evaluate.py so the command module stays a thin Click
shell. The LLM/service imports inside ``build_and_run`` stay deferred so that
scan/digest/migrate load without an LLM toolchain (and ``ml-gate`` stays lazy).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import click

from jobfeed.cli import AppContext
from jobfeed.cli.ml_gate import build_hard_filters, build_ml_gate, needs_ml_gate
from jobfeed.domain.models import PipelineRun


def _templates_dir() -> Path:
    """Resolve the package templates directory.

    Returns:
        Absolute path to ``src/jobfeed/templates``.
    """
    return Path(__file__).resolve().parents[1] / "templates"


@dataclass(frozen=True, kw_only=True)
class _EvalParams:
    """Typed container for evaluate CLI flags."""

    stage: str
    corpus: str
    limit: int | None
    max_days: int | None
    parallel: int | None
    threshold: int | None
    dry_run: bool


async def build_and_run(
    app: AppContext,
    params: _EvalParams,
) -> PipelineRun:
    """Construct dependencies and run evaluation.

    Imports are deferred so the CLI module loads without LLM
    dependencies for non-evaluate commands.

    Args:
        app: Initialized application context.
        params: Typed evaluate CLI flags.

    Returns:
        Pipeline run with counters.

    Raises:
        click.ClickException: If the master resume file is missing on a
            non-dry run.
    """
    from jobfeed.adapters.llm._factory import (  # noqa: PLC0415
        LLMClientBuildOptions,
        build_llm_client,
    )
    from jobfeed.adapters.llm._pricing import (  # noqa: PLC0415
        load_price_table,
    )
    from jobfeed.adapters.llm._prompts import (  # noqa: PLC0415
        JinjaPromptRenderer,
    )
    from jobfeed.adapters.llm.mock import MockLLM  # noqa: PLC0415
    from jobfeed.ports.store_ops import StoreOpsMixin  # noqa: PLC0415
    from jobfeed.ports.store_status import StoreStatusMixin  # noqa: PLC0415
    from jobfeed.services.evaluate import (  # noqa: PLC0415
        EvaluateService,
    )
    from jobfeed.services.evaluate_types import (  # noqa: PLC0415
        EvaluateDependencies,
        EvaluateLLMConfig,
        EvaluateRuntimeConfig,
    )

    settings = app["settings"]
    llm_settings = settings.llm
    if params.parallel is not None:
        llm_settings = llm_settings.model_copy(
            update={"max_concurrent": params.parallel},
        )

    resume_text = ""
    if not params.dry_run:
        resume_path = Path(llm_settings.master_resume_path)
        if not resume_path.is_file():
            raise click.ClickException(f"Resume file not found: {resume_path}")
        resume_text = resume_path.read_text(encoding="utf-8")

    logger = app["logger"]
    price_table = load_price_table()
    unused_llm = MockLLM()
    needs_stage_a = not params.dry_run and params.stage != "b"
    needs_stage_b = not params.dry_run and params.stage != "a"
    primary_options = LLMClientBuildOptions(max_retries=0)
    llm_a = (
        build_llm_client(
            llm_settings.stage_a,
            settings=llm_settings,
            price_table=price_table,
            logger=logger,
            options=primary_options,
        )
        if needs_stage_a
        else unused_llm
    )
    llm_b = (
        build_llm_client(
            llm_settings.stage_b,
            settings=llm_settings,
            price_table=price_table,
            logger=logger,
            options=primary_options,
        )
        if needs_stage_b
        else unused_llm
    )

    llm_b_sweep = None
    if needs_stage_b:
        llm_b_sweep = build_llm_client(
            llm_settings.stage_b,
            settings=llm_settings,
            price_table=price_table,
            logger=logger,
            options=LLMClientBuildOptions(timeout_s=None, max_retries=0),
        )

    preamble = (
        Path(llm_settings.preamble_personal_path)
        if llm_settings.preamble_personal_path
        else None
    )
    prompt_renderer = JinjaPromptRenderer(
        templates_dir=_templates_dir(),
        preamble_path=preamble,
    )

    store = app["store"]
    threshold = (
        params.threshold
        if params.threshold is not None
        else settings.scoring.stage_a_threshold
    )
    ml_gate_enabled = settings.scoring.ml_gate_enabled
    needs_gate = needs_ml_gate(
        params.stage, params.limit, ml_gate_enabled=ml_gate_enabled
    )
    service = EvaluateService(
        deps=EvaluateDependencies(
            store=store,
            store_ops=cast(StoreOpsMixin, store),
            store_status=cast(StoreStatusMixin, store),
            prompt_renderer=prompt_renderer,
            llm_stage_a=llm_a,
            llm_stage_b=llm_b,
            llm_stage_b_sweep=llm_b_sweep,
            ml_gate=build_ml_gate(settings) if needs_gate else None,
            hard_filters=build_hard_filters(settings),
        ),
        config=EvaluateRuntimeConfig(
            llm=EvaluateLLMConfig(
                stage_a=llm_settings.stage_a,
                stage_b=llm_settings.stage_b,
                max_concurrent=llm_settings.max_concurrent,
                max_daily_score_calls=llm_settings.max_daily_score_calls,
                max_daily_cost_usd=llm_settings.max_daily_cost_usd,
            ),
            stage_a_threshold=threshold,
            resume_text=resume_text,
            default_eval_limit=settings.scoring.default_eval_limit,
            ml_gate_enabled=ml_gate_enabled,
            ml_gate_max_candidates=settings.ml_gate.max_candidates,
        ),
        logger=logger,
    )
    return await service.run(
        stage=params.stage,
        corpus=params.corpus,
        limit=params.limit,
        max_days=params.max_days,
        dry_run=params.dry_run,
    )
