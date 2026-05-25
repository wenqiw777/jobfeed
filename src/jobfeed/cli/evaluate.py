"""Click command for LLM-backed job evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models import PipelineRun

# Sweep adapter uses a large timeout instead of unbounded None because the
# adapter constructors accept ``float`` (not ``float | None``).
_SWEEP_TIMEOUT_S = 3600.0


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
    type=int,
    help="LLM concurrency (default: from config).",
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
    type=int,
    help="Maximum jobs to evaluate per stage.",
)
@click.option(
    "--max-days",
    default=None,
    type=int,
    help="Freshness filter on discovered_at.",
)
@click.option(
    "--threshold",
    default=None,
    type=int,
    help="Stage A threshold override.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List pending work without calling the LLM.",
)
@click.pass_context
def evaluate(  # noqa: PLR0913 - Click handler receives one param per flag
    ctx: click.Context,
    parallel: int | None,
    stage: str,
    corpus: str,
    limit: int | None,
    max_days: int | None,
    threshold: int | None,
    dry_run: bool,
) -> None:
    """Run pending evaluation stages and print counters.

    LLM clients, prompt renderer, and EvaluateService are built lazily
    so that scan/digest/migrate work without LLM CLI tools installed.

    Args:
        ctx: Click invocation context.
        parallel: LLM concurrency override.
        stage: Which stage(s) to run.
        corpus: Which jobs to select.
        limit: Max jobs per stage.
        max_days: Freshness filter.
        threshold: Stage A score threshold override.
        dry_run: Skip LLM calls and persistence of scores.
    """
    app = require_app(ctx)
    params = _EvalParams(
        stage=stage,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
        parallel=parallel,
        threshold=threshold,
        dry_run=dry_run,
    )
    run = asyncio.run(_run_evaluate(app, params))
    click.echo(
        f"Evaluated {run.stage_a_scored} (Stage A), {run.stage_b_scored} (Stage B)"
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
        build_llm_client,
    )
    from jobfeed.adapters.llm._pricing import (  # noqa: PLC0415
        load_price_table,
    )
    from jobfeed.adapters.llm._prompts import (  # noqa: PLC0415
        JinjaPromptRenderer,
    )
    from jobfeed.ports.store_ops import StoreOpsMixin  # noqa: PLC0415
    from jobfeed.services.evaluate import (  # noqa: PLC0415
        EvaluateDependencies,
        EvaluateRuntimeConfig,
        EvaluateService,
    )

    settings = app["settings"]
    llm_settings = settings.llm
    if params.parallel is not None:
        llm_settings = llm_settings.model_copy(
            update={"max_concurrent": params.parallel},
        )

    # Validate resume file
    resume_path = Path(llm_settings.master_resume_path)
    if not resume_path.is_file():
        raise click.ClickException(f"Resume file not found: {resume_path}")
    resume_text = resume_path.read_text(encoding="utf-8")

    # Build LLM clients lazily
    price_table = load_price_table()
    logger = app["logger"]
    llm_a = build_llm_client(
        llm_settings.stage_a,
        settings=llm_settings,
        price_table=price_table,
        logger=logger,
    )
    llm_b = build_llm_client(
        llm_settings.stage_b,
        settings=llm_settings,
        price_table=price_table,
        logger=logger,
    )

    # Build sweep adapter with large timeout (Decision 12)
    sweep_settings = llm_settings.model_copy(
        update={
            "codex_timeout_s": _SWEEP_TIMEOUT_S,
            "claude_timeout_s": _SWEEP_TIMEOUT_S,
        },
    )
    llm_b_sweep = build_llm_client(
        llm_settings.stage_b,
        settings=sweep_settings,
        price_table=price_table,
        logger=logger,
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
            llm=llm_settings,
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
