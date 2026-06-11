"""Click commands for JD enrichment: enrich-paste and enrich-linkedin-guest."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import click

from jobfeed.adapters.sources._linkedin_guest_http import (
    GuestResponse,
    create_client,
    fetch,
)
from jobfeed.adapters.sources.linkedin_guest import LinkedInGuestEnricher
from jobfeed.cli import AppContext, require_app, require_enabled, run_with_store
from jobfeed.config_sources import SourcesLinkedInGuestConfig
from jobfeed.domain.quality import assess_quality
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.enrich import EnrichPacing, EnrichService, EnrichSummary

_PLATFORM_CHOICES = ("linkedin", "indeed")


@click.command(name="enrich-paste", help="Paste a JD manually for a stub job.")
@click.argument("canonical_id")
@click.option(
    "--platform",
    type=click.Choice(_PLATFORM_CHOICES),
    default="linkedin",
    show_default=True,
    help="Source platform of the job.",
)
@click.option(
    "--from-file",
    "from_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Read the JD from a host file (when running via ./bin/jobfeed, "
        "pipe the file to stdin instead)."
    ),
)
@click.pass_context
def enrich_paste(
    ctx: click.Context,
    canonical_id: str,
    platform: str,
    from_file: Path | None,
) -> None:
    """Store manually pasted JD text for an existing job.

    Args:
        ctx: Click invocation context.
        canonical_id: Platform-specific job identity.
        platform: Source platform of the job.
        from_file: Optional file to read the JD text from (default: stdin).

    Raises:
        click.UsageError: If the JD text is empty or whitespace-only.
    """
    app = require_app(ctx)
    jd_text = _load_jd_text(from_file)
    if not jd_text.strip():
        raise click.UsageError("JD text is empty")
    asyncio.run(
        _run_enrich_paste(
            app,
            platform=platform,
            canonical_id=canonical_id,
            jd_text=jd_text,
        )
    )


def _load_jd_text(from_file: Path | None) -> str:
    """Read JD text from a file when given, else from stdin.

    Args:
        from_file: Optional path to a JD text file.

    Returns:
        Raw JD text.

    Raises:
        click.ClickException: If the file cannot be read.
    """
    if from_file is None:
        return click.get_text_stream("stdin").read()
    try:
        return from_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"cannot read {from_file}: {exc}") from exc


async def _run_enrich_paste(
    app: AppContext,
    *,
    platform: str,
    canonical_id: str,
    jd_text: str,
) -> None:
    async def action() -> None:
        # An unknown canonical_id raises ValueError in the store, which
        # run_with_store surfaces as a clean nonzero ClickException.
        store = cast(StoreOpsMixin, app["store"])
        job_id = await store.enrich_paste(
            platform=platform,
            canonical_id=canonical_id,
            jd_text=jd_text,
        )
        quality = assess_quality(jd_text)
        click.echo(f"Enriched job {job_id} (quality: {quality})")

    await run_with_store(app, action)


@click.command(
    name="enrich-linkedin-guest",
    help="Fetch JDs for unenriched LinkedIn guest jobs (one paced pass).",
)
@click.pass_context
def enrich_linkedin_guest(ctx: click.Context) -> None:
    """Run one paced JD-enrichment pass over ``linkedin_guest`` rows.

    Args:
        ctx: Click invocation context.

    Raises:
        click.ClickException: If the linkedin_guest source is disabled.
    """
    app = require_app(ctx)
    config = app["settings"].sources.linkedin_guest
    require_enabled(config.enabled, "linkedin-guest")
    summary = asyncio.run(_run_enrich_linkedin_guest(app, config))
    click.echo(_format_enrich_summary(summary))


async def _run_enrich_linkedin_guest(
    app: AppContext,
    config: SourcesLinkedInGuestConfig,
) -> EnrichSummary:
    """Build the guest enricher + service and run one enrichment pass.

    The httpx client is opened here (proxy/timeout from config) and closed by
    the ``async with`` on every path, including service errors.
    """

    async def action() -> EnrichSummary:
        client = create_client(config.proxies, config.timeout_s)
        async with client:

            async def fetch_url(url: str) -> GuestResponse:
                return await fetch(client, url)

            service = EnrichService(
                enricher=LinkedInGuestEnricher(fetcher=fetch_url, logger=app["logger"]),
                store=cast(StoreOpsMixin, app["store"]),
                logger=app["logger"],
                pacing=EnrichPacing(min_interval_s=config.pacing_s),
            )
            return await service.run(
                platform="linkedin_guest",
                batch_limit=config.enrich_batch_limit,
            )

    return await run_with_store(app, action)


def _format_enrich_summary(summary: EnrichSummary) -> str:
    """Render the pass counters; ``blocked`` counts block events."""
    line = (
        f"Enriched {summary.enriched}, closed {summary.closed}, "
        f"blocked {summary.blocked} (block events), skipped {summary.skipped}"
    )
    if summary.stopped_early:
        line += " — stopped early (rate-limited; re-run later)"
    return line


__all__ = ["enrich_linkedin_guest", "enrich_paste"]
