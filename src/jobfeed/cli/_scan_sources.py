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

import httpx

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.jobright import JobrightSource
from jobfeed.adapters.sources.linkedin import LinkedInSource
from jobfeed.adapters.sources.linkedin_guest import (
    GuestSourceSettings,
    LinkedInGuestSource,
)
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.cli import AppContext, require_enabled
from jobfeed.domain.models import CompanyRecord
from jobfeed.ports.source import ClosedJobLookup, EnrichmentLookup
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.scan import SourceSpec

# Real (non-mock) sources eligible for ``--source all`` fan-out, in scan order.
_REAL_SOURCES = (
    "ats",
    "speedyapply",
    "indeed",
    "linkedin-guest",
    "linkedin",
    "jobright",
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
        raise ValueError("Mock source not configured")
    return ("mock", mock_source, {})


async def _build_ats(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Build ATSSource + its httpx client; append to sources, own the client."""
    ats_config = app["settings"].sources.ats
    require_enabled(ats_config.enabled, "ats")
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
    require_enabled(config.enabled, "speedyapply")
    client = _register_client(stack, create_http_client(config.fetch_timeout_s))
    # The runtime store implements ClosedJobLookup; passing it lets the source
    # skip re-fetching JDs for postings already stamped closed_at (dead links).
    closed_lookup = cast(ClosedJobLookup, app["store"])
    sources.append(
        (
            "speedyapply",
            SpeedyApplySource(
                client=client,
                config=config,
                logger=app["logger"],
                closed_lookup=closed_lookup,
            ),
            {},
        )
    )


async def _build_indeed(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,  # noqa: ARG001 - no client to own
) -> None:
    """Build the Indeed JobSpy source (no httpx client; scrape subprocess-owned)."""
    config = app["settings"].sources.indeed
    require_enabled(config.enabled, "indeed")
    sources.append(
        (
            "indeed",
            IndeedSource(config=config, logger=app["logger"]),
            {},
        )
    )


async def _build_linkedin_guest(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,  # noqa: ARG001 - client is source-owned
) -> None:
    """Build the anonymous guest-endpoint source (it owns its own client)."""
    config = app["settings"].sources.linkedin_guest
    require_enabled(config.enabled, "linkedin-guest")
    sources.append(
        (
            "linkedin_guest",
            LinkedInGuestSource(
                settings=GuestSourceSettings(
                    search_urls=config.search_urls,
                    max_jobs=config.max_jobs,
                    posted_within_hours=(
                        app["settings"].hard_filters.posted_within_hours
                    ),
                    pacing_s=config.pacing_s,
                    proxies=config.proxies,
                    timeout_s=config.timeout_s,
                ),
                logger=app["logger"],
            ),
            {},
        )
    )


async def _build_linkedin(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,  # noqa: ARG001 - Playwright context is source-owned
) -> None:
    config = app["settings"].sources.linkedin
    require_enabled(config.enabled, "linkedin")
    # The runtime store implements EnrichmentLookup; passing it lets the session
    # skip re-enriching postings whose JD is already fresh in the store.
    freshness = cast(EnrichmentLookup, app["store"])
    sources.append(
        (
            "linkedin",
            LinkedInSource(config=config, logger=app["logger"], freshness=freshness),
            {},
        )
    )


async def _build_jobright(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Build the Chrome-extension-backed personalized recommendation source."""
    config = app["settings"].sources.jobright
    require_enabled(config.enabled, "jobright")
    client = _register_client(stack, create_http_client(30.0))
    sources.append(
        (
            "jobright",
            JobrightSource(
                config=config,
                bridge=app["jobright_bridge"],
                logger=app["logger"],
                client=client,
            ),
            {},
        )
    )


async def _noop_builder(
    app: AppContext,
    sources: list[SourceSpec],
    stack: contextlib.AsyncExitStack,
) -> None:
    """Builder for tokens with no extra source to construct (e.g. ``mock``)."""


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
    "linkedin-guest": _build_linkedin_guest,
    "linkedin": _build_linkedin,
    "jobright": _build_jobright,
}

# Real source token -> its field name on ``settings.sources`` (the hyphenated
# CLI token differs from the snake_case config field).
_CONFIG_FIELDS = {
    "ats": "ats",
    "speedyapply": "speedyapply",
    "indeed": "indeed",
    "linkedin-guest": "linkedin_guest",
    "linkedin": "linkedin",
    "jobright": "jobright",
}


__all__ = ["build_scan_sources", "seed_ats_companies"]
