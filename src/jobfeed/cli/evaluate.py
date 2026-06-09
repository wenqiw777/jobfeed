"""Click command for LLM-backed job evaluation."""

from __future__ import annotations

import asyncio
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli._evaluate_build import _EvalParams, build_and_run
from jobfeed.domain.models import PipelineRun


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
        return await build_and_run(app, params)

    return await run_with_store(app, action)


__all__ = ["evaluate"]
