"""Click command for source scanning."""

from __future__ import annotations

import asyncio
import contextlib

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli._scan_sources import build_scan_sources
from jobfeed.domain.models import PipelineRun

# CLI source tokens. "all" fans out to every REAL source whose config is
# enabled; the mock source is a dev seed and is EXPLICIT-ONLY (--source mock),
# never folded into "all" — otherwise every real scan would persist synthetic
# mock jobs. The hyphenated "linkedin-jobspy" token maps to the
# ``linkedin_jobspy`` config field and the ``LinkedInJobSpySource`` platform tag.
# The plain "linkedin" token is the authenticated Playwright SessionSource.
SOURCE_CHOICES = [
    "mock",
    "ats",
    "speedyapply",
    "indeed",
    "linkedin-jobspy",
    "linkedin",
    "all",
]


@click.command(name="scan", help="Fetch jobs from a configured source.")
@click.option(
    "--source",
    "source_name",
    default="all",
    show_default=True,
    type=click.Choice(SOURCE_CHOICES),
    help="Source adapter to scan: mock, ats, speedyapply, indeed, "
    "linkedin-jobspy, linkedin, or all (default: all enabled real sources; "
    "mock is explicit-only).",
)
@click.pass_context
def scan(ctx: click.Context, source_name: str) -> None:
    """Run one source scan and print persistence counters.

    Args:
        ctx: Click invocation context.
        source_name: Source adapter token from ``SOURCE_CHOICES``.
    """
    app = require_app(ctx)
    run = asyncio.run(_run_scan(app, source_name))
    click.echo(
        "Discovered "
        f"{run.jobs_discovered} jobs, inserted {run.jobs_inserted}, "
        f"updated {run.jobs_updated}"
    )


async def _run_scan(app: AppContext, source_name: str) -> PipelineRun:
    async def action() -> PipelineRun:
        # One AsyncExitStack owns EVERY resource built for this scan (notably
        # every httpx client from ATS/SpeedyApply). ``--source all`` can build
        # multiple clients; registering each on the stack guarantees they all
        # close even when several real sources run together.
        async with contextlib.AsyncExitStack() as stack:
            sources = await build_scan_sources(app, source_name, stack)
            return await app["scan_service"].run(sources)

    return await run_with_store(app, action)


__all__ = ["scan"]
