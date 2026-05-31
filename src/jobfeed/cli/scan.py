"""Click command for source scanning."""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

import click
import httpx

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.linkedin_jobspy import LinkedInJobSpySource
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models import CompanyRecord, PipelineRun
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.scan import SourceSpec

# CLI source tokens. "all" fans out to every REAL source whose config is
# enabled; the mock source is a dev seed and is EXPLICIT-ONLY (--source mock),
# never folded into "all" — otherwise every real scan would persist synthetic
# mock jobs. The hyphenated "linkedin-jobspy" token maps to the
# ``linkedin_jobspy`` config field and the ``LinkedInJobSpySource`` platform tag
# — Phase 4b adds a separate Playwright "linkedin" choice, not wired here.
SOURCE_CHOICES = ["mock", "ats", "speedyapply", "indeed", "linkedin-jobspy", "all"]

# Real (non-mock) sources eligible for ``--source all`` fan-out, in scan order.
_REAL_SOURCES = ("ats", "speedyapply", "indeed", "linkedin-jobspy")


@click.command(name="scan", help="Fetch jobs from a configured source.")
@click.option(
    "--source",
    "source_name",
    default="all",
    show_default=True,
    type=click.Choice(SOURCE_CHOICES),
    help="Source adapter to scan: mock, ats, speedyapply, indeed, "
    "linkedin-jobspy, or all (default: all enabled real sources; mock is "
    "explicit-only).",
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


async def _run_scan(app: AppContext, source_name: str) -> PipelineRun:
    async def action() -> PipelineRun:
        # One AsyncExitStack owns EVERY resource built for this scan (notably
        # every httpx client from ATS/SpeedyApply). ``--source all`` can build
        # multiple clients; registering each on the stack guarantees they all
        # close even when several real sources run together.
        async with contextlib.AsyncExitStack() as stack:
            sources = await _build_sources(app, source_name, stack)
            return await app["scan_service"].run(sources)

    return await run_with_store(app, action)


async def _build_sources(
    app: AppContext,
    source_name: str,
    stack: contextlib.AsyncExitStack,
) -> list[SourceSpec]:
    """Resolve the source token into the concrete ``SourceSpec`` list.

    Sources are built LOCALLY here (never stored into ``app["sources"]`` — that
    registry stays the mock seed). Each builder appends to ``sources`` and
    registers any owned httpx client on ``stack``.

    Args:
        app: Initialized application context.
        source_name: CLI source token from ``SOURCE_CHOICES``.
        stack: Exit stack owning every client created for this scan.

    Returns:
        Source specs to hand to ``ScanService.run``.
    """
    sources: list[SourceSpec] = []
    if source_name == "mock":  # dev seed — explicit only, never part of "all"
        sources.append(_resolve_mock_source(app))
    if source_name == "all":
        await _build_enabled_real_sources(app, sources, stack)
        return sources
    await _BUILDERS.get(source_name, _noop_builder)(app, sources, stack)
    return sources


async def _build_enabled_real_sources(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Append each enabled real source for ``--source all``; log the skips.

    Disabled sources are never silently omitted — each emits a structured
    ``scan_source_skipped`` event so an operator sees exactly which sources a
    given config left out.
    """
    for name in _REAL_SOURCES:
        if _is_source_enabled(app, name):
            await _BUILDERS[name](app, sources, stack)
        else:
            app["logger"].info("scan_source_skipped", source=name, reason="disabled")


def _is_source_enabled(app: AppContext, source_name: str) -> bool:
    """Return whether the real source's config has ``enabled = true``."""
    config = getattr(app["settings"].sources, _CONFIG_FIELDS[source_name])
    return bool(config.enabled)


def _resolve_mock_source(app: AppContext) -> SourceSpec:
    """Look up the mock source from app context."""
    mock_source = app["sources"].get("mock")
    if mock_source is None:
        raise click.ClickException("Mock source not configured")
    return ("mock", mock_source, {})


async def _build_ats(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Build ATSSource + its httpx client; append to sources, own the client."""
    ats_config = app["settings"].sources.ats
    _require_enabled(ats_config.enabled, "ats")
    store_ops = cast(StoreOpsMixin, app["store"])
    await seed_ats_companies(store_ops, ats_config.seed_companies)
    client = _register_client(stack, create_http_client(ats_config.scan_timeout_s))
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


async def _build_speedyapply(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Build SpeedyApplySource + its httpx client; own the client on the stack."""
    config = app["settings"].sources.speedyapply
    _require_enabled(config.enabled, "speedyapply")
    client = _register_client(stack, create_http_client(config.fetch_timeout_s))
    sources.append(
        (
            "speedyapply",
            SpeedyApplySource(client=client, config=config, logger=app["logger"]),
            {},
        )
    )


async def _build_indeed(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,  # noqa: ARG001 - no client to own
) -> None:
    """Build the Indeed JobSpy source (no httpx client; scrape via to_thread)."""
    config = app["settings"].sources.indeed
    _require_enabled(config.enabled, "indeed")
    sources.append(
        (
            "indeed",
            IndeedSource(config=config, logger=app["logger"]),
            {},
        )
    )


async def _build_linkedin_jobspy(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,  # noqa: ARG001 - no client to own
) -> None:
    """Build the LinkedIn JobSpy source (no httpx client; scrape via to_thread)."""
    config = app["settings"].sources.linkedin_jobspy
    _require_enabled(config.enabled, "linkedin-jobspy")
    sources.append(
        (
            "linkedin_jobspy",
            LinkedInJobSpySource(config=config, logger=app["logger"]),
            {},
        )
    )


async def _noop_builder(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Builder for tokens with no extra source to construct (e.g. ``mock``)."""


def _require_enabled(enabled: bool, source_name: str) -> None:
    """Raise a ClickException when an explicitly requested source is disabled.

    Mirrors ``_build_ats``'s original disabled-source behavior for every source.
    """
    if not enabled:
        raise click.ClickException(f"{source_name} source is disabled in config")


def _register_client(
    stack: contextlib.AsyncExitStack,
    client: httpx.AsyncClient,
) -> httpx.AsyncClient:
    """Register an httpx client's ``aclose`` on the exit stack and return it."""
    stack.push_async_callback(client.aclose)
    return client


# Source token -> builder. A new source is one entry here + one Choice token +
# one ``_CONFIG_FIELDS`` entry (extensibility contract).
_BUILDERS = {
    "mock": _noop_builder,
    "ats": _build_ats,
    "speedyapply": _build_speedyapply,
    "indeed": _build_indeed,
    "linkedin-jobspy": _build_linkedin_jobspy,
}

# Real source token -> its field name on ``settings.sources`` (the hyphenated
# CLI token differs from the snake_case config field).
_CONFIG_FIELDS = {
    "ats": "ats",
    "speedyapply": "speedyapply",
    "indeed": "indeed",
    "linkedin-jobspy": "linkedin_jobspy",
}


__all__ = ["SOURCE_CHOICES", "scan", "seed_ats_companies"]
