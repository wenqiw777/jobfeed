"""Click command for LLM-backed job evaluation."""

from __future__ import annotations

import asyncio

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models import PipelineRun


@click.command(
    name="evaluate", help="Evaluate pending jobs through configured LLM stages."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List pending work without calling the LLM.",
)
@click.pass_context
def evaluate(ctx: click.Context, dry_run: bool) -> None:
    """Run pending evaluation stages and print stage-specific counters.

    Args:
        ctx: Click invocation context.
        dry_run: Whether to skip LLM calls and persistence of scores.
    """
    app = require_app(ctx)
    run = asyncio.run(_run_evaluate(app, dry_run=dry_run))
    click.echo(
        f"Evaluated {run.stage_a_scored} (Stage A), {run.stage_b_scored} (Stage B)"
    )


async def _run_evaluate(app: AppContext, *, dry_run: bool) -> PipelineRun:
    return await run_with_store(
        app,
        lambda: app["evaluate_service"].run(dry_run=dry_run),
    )


__all__ = ["evaluate"]
