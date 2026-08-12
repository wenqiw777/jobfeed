"""Shared EvaluateService construction logic usable from CLI and web layers.

Extracts the dependency-wiring that previously lived only in the Click-coupled
``cli/_evaluate_build.py`` so that the web API (RunManager) can build an
EvaluateService without any Click dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from jobfeed.domain.errors import ResumeNotConfiguredError

if TYPE_CHECKING:
    from jobfeed.cli import AppContext
    from jobfeed.services.evaluate import EvaluateService


def _templates_dir() -> Path:
    """Resolve the package templates directory.

    Returns:
        Absolute path to ``src/jobfeed/templates``.
    """
    return Path(__file__).resolve().parents[1] / "templates"


@dataclass(frozen=True, kw_only=True)
class EvalBuildParams:
    """Typed container for evaluate build parameters (no Click dependency)."""

    stage: str
    corpus: str
    limit: int | None
    max_days: int | None
    parallel: int | None = None
    threshold: int | None = None
    dry_run: bool = False


def build_evaluate_service(
    app: AppContext,
    params: EvalBuildParams,
) -> EvaluateService:
    """Construct an EvaluateService from the app context without Click.

    Imports are deferred so non-evaluate code paths never load the LLM
    toolchain.

    Args:
        app: Initialized application context (settings, store, logger).
        params: Build parameters for the evaluate service.

    Returns:
        A ready-to-run EvaluateService.

    Raises:
        ResumeNotConfiguredError: If the master resume file is missing on
            a non-dry run.
    """
    from jobfeed.adapters.llm._factory import (  # noqa: PLC0415
        LLMClientBuildOptions,
        build_llm_client,
    )
    from jobfeed.adapters.llm._pricing import load_price_table  # noqa: PLC0415
    from jobfeed.adapters.llm._prompts import JinjaPromptRenderer  # noqa: PLC0415
    from jobfeed.adapters.llm.mock import MockLLM  # noqa: PLC0415
    from jobfeed.cli.ml_gate import (  # noqa: PLC0415
        build_hard_filters,
        build_ml_gate,
        needs_ml_gate,
    )
    from jobfeed.ports.store_ops import StoreOpsMixin  # noqa: PLC0415
    from jobfeed.ports.store_status import StoreStatusMixin  # noqa: PLC0415
    from jobfeed.services.evaluate import EvaluateService  # noqa: PLC0415
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

    resume_text = _load_resume_text(llm_settings.master_resume_path, params.dry_run)
    logger = app["logger"]
    price_table = load_price_table()
    unused_llm = MockLLM()
    needs_a = not params.dry_run and params.stage != "b"
    needs_b = not params.dry_run and params.stage != "a"
    primary_options = LLMClientBuildOptions(max_retries=0)
    llm_a = (
        build_llm_client(
            llm_settings.stage_a,
            settings=llm_settings,
            price_table=price_table,
            logger=logger,
            options=primary_options,
        )
        if needs_a
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
        if needs_b
        else unused_llm
    )

    llm_b_sweep = None
    if needs_b:
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
    return EvaluateService(
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
            stage_b_threshold_sync=app.get("stage_b_threshold_sync"),
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
        run_orchestrator=app.get("run_orchestrator"),
        logger=logger,
    )


def _load_resume_text(resume_path_str: str, is_dry_run: bool) -> str:
    """Load the master resume text, or return empty for dry runs.

    Args:
        resume_path_str: Path to the resume file from config.
        is_dry_run: Whether this is a dry-run invocation.

    Returns:
        Resume file contents, or empty string for dry runs.

    Raises:
        ResumeNotConfiguredError: If the resume file is missing on a
            non-dry run.
    """
    if is_dry_run:
        return ""
    resume_path = Path(resume_path_str)
    if not resume_path.is_file():
        msg = f"Resume file not found: {resume_path}"
        raise ResumeNotConfiguredError(msg)
    return resume_path.read_text(encoding="utf-8")


__all__ = ["EvalBuildParams", "build_evaluate_service"]
