"""Source-building composition for the scan command.

Resolves a ``--source`` token into the ``SourceSpec`` entries ScanService runs:
per-vendor builders (each owning any httpx client on the caller's
AsyncExitStack), the ``--source all`` enabled fan-out, ATS company seeding, and
the token->builder / token->config-field registries. Kept out of cli/scan.py so
the command module stays a thin Click shell.
"""

from __future__ import annotations

import contextlib
from typing import cast

import click
import httpx

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.linkedin import LinkedInSource
from jobfeed.adapters.sources.linkedin_jobspy import LinkedInJobSpySource
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.cli import AppContext
from jobfeed.domain.models import CompanyRecord
from jobfeed.ports.source import EnrichmentLookup
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.scan import SourceSpec

# Real (non-mock) sources eligible for ``--source all`` fan-out, in scan order.
_REAL_SOURCES = ("ats", "speedyapply", "indeed", "linkedin-jobspy", "linkedin")


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


async def build_scan_sources(
    app: AppContext,
    source_name: str,
    stack: contextlib.AsyncExitStack,
) -> list[SourceSpec]:
    """Resolve the ``--source`` token into locally built ``SourceSpec`` entries.

    Args:
        app: Initialized application context.
        source_name: Source token from ``SOURCE_CHOICES``.
        stack: Exit stack that owns any httpx clients built for the scan.

    Returns:
        Source specs for ScanService to run (empty for an unknown token).
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
    """Append enabled real sources for ``--source all`` and log skips."""
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


async def _build_linkedin(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,  # noqa: ARG001 - Playwright context is source-owned
) -> None:
    config = app["settings"].sources.linkedin
    _require_enabled(config.enabled, "linkedin")
    # The PostgresStore implements EnrichmentLookup; passing it lets the session
    # skip re-enriching postings whose JD is already fresh in the store.
    freshness = cast(EnrichmentLookup, app["store"])
    sources.append(
        (
            "linkedin",
            LinkedInSource(config=config, logger=app["logger"], freshness=freshness),
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
    "linkedin": _build_linkedin,
}

# Real source token -> its field name on ``settings.sources`` (the hyphenated
# CLI token differs from the snake_case config field).
_CONFIG_FIELDS = {
    "ats": "ats",
    "speedyapply": "speedyapply",
    "indeed": "indeed",
    "linkedin-jobspy": "linkedin_jobspy",
    "linkedin": "linkedin",
}


__all__ = ["build_scan_sources", "seed_ats_companies"]
