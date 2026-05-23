"""Click command for source scanning."""

from __future__ import annotations

import asyncio
from typing import cast

import click
import httpx

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models import CompanyRecord, PipelineRun
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.scan import SourceSpec


@click.command(name="scan", help="Fetch jobs from a configured source.")
@click.option(
    "--source",
    "source_name",
    default="mock",
    show_default=True,
    type=click.Choice(["mock", "ats", "all"]),
    help="Source adapter to scan: mock, ats, or all.",
)
@click.pass_context
def scan(ctx: click.Context, source_name: str) -> None:
    """Run one source scan and print persistence counters.

    Args:
        ctx: Click invocation context.
        source_name: Source adapter name — mock, ats, or all.
    """
    app = require_app(ctx)
    run = asyncio.run(_run_scan(app, source_name))
    click.echo(
        "Discovered "
        f"{run.jobs_discovered} jobs, inserted {run.jobs_inserted}, "
        f"updated {run.jobs_updated}"
    )


async def seed_ats_companies(
    store: StoreOpsMixin,
    slugs: list[str],
) -> None:
    """Seed company rows for each slug if they do not already exist.

    Idempotent: existing rows (with any probe cache, scan counts, or failure
    counters) are left untouched. Only absent slugs are inserted.

    Args:
        store: Store adapter implementing StoreOpsMixin.
        slugs: Company slugs from ``settings.sources.ats.seed_companies``.
    """
    for slug in slugs:
        existing = await store.get_company(slug)
        if existing is None:
            await store.upsert_company(CompanyRecord(slug=slug))


def _resolve_mock_source(app: AppContext) -> SourceSpec:
    """Look up the mock source from app context."""
    mock_source = app["sources"].get("mock")
    if mock_source is None:
        raise click.ClickException("Mock source not configured")
    return ("mock", mock_source, {})


async def _run_scan(app: AppContext, source_name: str) -> PipelineRun:
    async def action() -> PipelineRun:
        sources: list[SourceSpec] = []
        client = None
        try:
            if source_name in ("mock", "all"):
                sources.append(_resolve_mock_source(app))
            if source_name in ("ats", "all"):
                client = await _build_ats(app, sources)
            return await app["scan_service"].run(sources)
        finally:
            if client is not None:
                await client.aclose()

    return await run_with_store(app, action)


async def _build_ats(app: AppContext, sources: list[SourceSpec]) -> httpx.AsyncClient:
    """Create ATSSource and its httpx client, append to sources list."""
    ats_config = app["settings"].sources.ats
    if not ats_config.enabled:
        raise click.ClickException("ATS source is disabled in config")
    store_ops = cast(StoreOpsMixin, app["store"])
    await seed_ats_companies(store_ops, ats_config.seed_companies)
    client = create_http_client(ats_config.scan_timeout_s)
    sources.append(
        (
            "ats",
            ATSSource(
                client=client,
                store=store_ops,
                config=ats_config,
                logger=app["logger"],
            ),
            {},
        )
    )
    return client


__all__ = ["scan", "seed_ats_companies"]
