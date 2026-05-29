"""Click command for LLM-backed job evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
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


@click.command(
    name="evaluate",
    help="Evaluate pending jobs through configured LLM stages.",
)
@click.option(
    "-j",
    "--parallel",
    default=None,
    type=click.IntRange(min=1, max=32),
    help="LLM concurrency, 1-32 (default: from config).",
)
@click.option(
    "--stage",
    type=click.Choice(["a", "b", "both"]),
    default="both",
    help="Which evaluation stage(s) to run.",
)
@click.option(
    "--corpus",
    type=click.Choice(["unrated", "all", "failed"]),
    default="unrated",
    help="Which jobs to evaluate.",
)
@click.option(
    "--limit",
    default=None,
    type=click.IntRange(min=0),
    help="Maximum jobs to evaluate per stage.",
)
@click.option(
    "--max-days",
    default=None,
    type=click.IntRange(min=0),
    help="Freshness filter on discovered_at.",
)
@click.option(
    "--threshold",
    default=None,
    type=click.IntRange(min=0, max=100),
    help="Stage A threshold override.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List pending work without calling the LLM.",
)
@click.pass_context
def evaluate(ctx: click.Context, /, **kwargs: object) -> None:
    """Run pending evaluation stages and print counters.

    LLM clients, prompt renderer, and EvaluateService are built lazily
    so that scan/digest/migrate work without LLM CLI tools installed.

    Args:
        ctx: Click invocation context.
        kwargs: Click option values keyed by option name.
    """
    app = require_app(ctx)
    params = _params_from_click(kwargs)
    run = asyncio.run(_run_evaluate(app, params))
    if params.dry_run:
        _print_dry_run_preview(run)
    click.echo(
        f"Evaluated {run.stage_a_scored} (Stage A), {run.stage_b_scored} (Stage B)"
    )


def _params_from_click(values: dict[str, object]) -> _EvalParams:
    """Normalize Click's untyped option mapping into typed evaluate params."""
    return _EvalParams(
        stage=cast(str, values["stage"]),
        corpus=cast(str, values["corpus"]),
        limit=cast(int | None, values["limit"]),
        max_days=cast(int | None, values["max_days"]),
        parallel=cast(int | None, values["parallel"]),
        threshold=cast(int | None, values["threshold"]),
        dry_run=cast(bool, values["dry_run"]),
    )


def _print_dry_run_preview(run: PipelineRun) -> None:
    for item in run.dry_run_preview:
        job_id = item.job_id or "unpersisted"
        click.echo(
            f"Would evaluate {item.stage}: {item.title} @ {item.company} [{job_id}]"
        )


async def _run_evaluate(
    app: AppContext,
    params: _EvalParams,
) -> PipelineRun:
    """Build EvaluateService lazily and run the evaluation pipeline.

    Args:
        app: Initialized application context.
        params: Typed evaluate CLI flags.

    Returns:
        Pipeline run with counters.
    """

    async def action() -> PipelineRun:
        return await _build_and_run(app, params)

    return await run_with_store(app, action)


async def _build_and_run(
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
    service = EvaluateService(
        deps=EvaluateDependencies(
            store=store,
            store_ops=cast(StoreOpsMixin, store),
            prompt_renderer=prompt_renderer,
            llm_stage_a=llm_a,
            llm_stage_b=llm_b,
            llm_stage_b_sweep=llm_b_sweep,
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


__all__ = ["evaluate"]
