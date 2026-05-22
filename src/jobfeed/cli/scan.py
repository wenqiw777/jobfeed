"""Click command for source scanning."""

from __future__ import annotations

import asyncio

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models import PipelineRun
from jobfeed.ports.source import SimpleSource
from jobfeed.services.scan import SourceSpec


@click.command(name="scan", help="Fetch jobs from a configured source.")
@click.option(
    "--source",
    "source_name",
    # TODO(phase1): Replace this skeleton default with config-selected sources.
    default="mock",
    show_default=True,
    help="Source adapter to scan.",
)
@click.pass_context
def scan(ctx: click.Context, source_name: str) -> None:
    """Run one source scan and print persistence counters.

    Args:
        ctx: Click invocation context.
        source_name: Configured source adapter name.
    """
    app = require_app(ctx)
    run = asyncio.run(_run_scan(app, source_name))
    click.echo(
        "Discovered "
        f"{run.jobs_discovered} jobs, inserted {run.jobs_inserted}, "
        f"updated {run.jobs_updated}"
    )


async def _run_scan(app: AppContext, source_name: str) -> PipelineRun:
    source = _source_by_name(app, source_name)
    source_specs: list[SourceSpec] = [(source_name, source, {})]
    return await run_with_store(
        app,
        lambda: app["scan_service"].run(source_specs),
    )


def _source_by_name(app: AppContext, source_name: str) -> SimpleSource:
    sources = app["sources"]
    source = sources.get(source_name)
    if source is None:
        raise click.ClickException(f"Unsupported source: {source_name}")
    return source


__all__ = ["scan"]
