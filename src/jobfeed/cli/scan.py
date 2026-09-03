"""Click command for source scanning."""

from __future__ import annotations

import asyncio
import contextlib

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli._scan_sources import build_scan_sources
from jobfeed.cli.enrich import format_enrich_summary, run_guest_enrich_pass
from jobfeed.domain.models import PipelineRun
from jobfeed.services.enrich import EnrichSummary
from jobfeed.services.scan import SourceSpec

# CLI source tokens. "all" fans out to every REAL source whose config is
# enabled; the mock source is a dev seed and is EXPLICIT-ONLY (--source mock),
# never folded into "all" — otherwise every real scan would persist synthetic
# mock jobs. "linkedin-guest" is the anonymous guest-endpoint scraper (config
# field ``linkedin_guest``, platform tag ``linkedin_guest``); the plain
# "linkedin" token is the authenticated Playwright SessionSource.
SOURCE_CHOICES = [
    "mock",
    "ats",
    "speedyapply",
    "indeed",
    "linkedin-guest",
    "linkedin",
    "jobright",
    "all",
]

_GUEST_SOURCE_NAME = "linkedin_guest"


@click.command(name="scan", help="Fetch jobs from a configured source.")
@click.option(
    "--source",
    "source_name",
    default="all",
    show_default=True,
    type=click.Choice(SOURCE_CHOICES),
    help="Source adapter to scan: mock, ats, speedyapply, indeed, "
    "linkedin-guest, linkedin, jobright, or all (default: all enabled "
    "real sources; mock is explicit-only).",
)
@click.pass_context
def scan(ctx: click.Context, source_name: str) -> None:
    """Run one source scan and print persistence counters.

    When the linkedin_guest source was scanned and its ``enrich_after_scan``
    config flag is on (the default), one paced JD-enrichment pass runs after
    the scan and its summary is printed as a second line.

    Args:
        ctx: Click invocation context.
        source_name: Source adapter token from ``SOURCE_CHOICES``.
    """
    app = require_app(ctx)
    run, guest_summary = asyncio.run(_run_scan(app, source_name))
    click.echo(
        "Discovered "
        f"{run.jobs_discovered} jobs, inserted {run.jobs_inserted}, "
        f"updated {run.jobs_updated}"
    )
    if guest_summary is not None:
        click.echo(f"LinkedIn guest enrich: {format_enrich_summary(guest_summary)}")


async def _run_scan(
    app: AppContext, source_name: str
) -> tuple[PipelineRun, EnrichSummary | None]:
    async def action() -> tuple[PipelineRun, EnrichSummary | None]:
        # One AsyncExitStack owns EVERY resource built for this scan (notably
        # every httpx client from ATS/SpeedyApply). ``--source all`` can build
        # multiple clients; registering each on the stack guarantees they all
        # close even when several real sources run together.
        async with contextlib.AsyncExitStack() as stack:
            sources = await build_scan_sources(app, source_name, stack)
            run = await app["scan_service"].run(sources)
            return run, await _enrich_guest_if_scanned(app, sources)

    return await run_with_store(app, action)


async def _enrich_guest_if_scanned(
    app: AppContext, sources: list[SourceSpec]
) -> EnrichSummary | None:
    """Run the default post-scan guest JD pass; contain its failures.

    Runs only when the linkedin_guest source was actually scanned AND the
    ``enrich_after_scan`` config flag is on. An enrich failure is logged and
    swallowed so the already-persisted scan results still report normally.
    """
    if all(name != _GUEST_SOURCE_NAME for name, _, _ in sources):
        return None
    config = app["settings"].sources.linkedin_guest
    if not config.enrich_after_scan:
        return None
    try:
        return await run_guest_enrich_pass(app, config)
    except Exception as exc:
        app["logger"].error("scan_guest_enrich_failed", error=str(exc))
        return None


__all__ = ["scan"]
