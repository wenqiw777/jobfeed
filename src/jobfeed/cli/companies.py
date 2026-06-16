"""Click commands for ATS company management: companies add/list/remove."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import click

from jobfeed.adapters.sources._ats_probe import probe_company
from jobfeed.adapters.sources._http import (
    ProbeIndeterminateError,
    ProbeNetworkError,
)
from jobfeed.adapters.sources.ats import SUPPORTED_VENDORS
from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli._probe import build_probe_company
from jobfeed.domain.models import CompanyRecord
from jobfeed.ports.store_ops import StoreOpsMixin

_VENDOR_CHOICES = sorted(SUPPORTED_VENDORS)


def _store_ops(app: AppContext) -> StoreOpsMixin:
    """Return the app store narrowed to the company-ops capability."""
    return cast(StoreOpsMixin, app["store"])


@click.group(name="companies", help="Manage tracked ATS companies.")
def companies() -> None:
    """ATS company management subcommands."""


# ── add ───────────────────────────────────────────────────────────────


@companies.command(name="add", help="Track a company by slug (auto-probe or pin).")
@click.argument("slug")
@click.option(
    "--ats",
    "vendor",
    type=click.Choice(_VENDOR_CHOICES),
    default=None,
    help="Pin the ATS vendor (skips auto-probe; never re-probed).",
)
@click.pass_context
def companies_add(ctx: click.Context, slug: str, vendor: str | None) -> None:
    """Add a company, probing for its ATS vendor unless one is pinned.

    Args:
        ctx: Click invocation context.
        slug: Company board slug.
        vendor: Optional pinned ATS vendor.
    """
    app = require_app(ctx)
    asyncio.run(_run_add(app, slug=slug, vendor=vendor))


async def _run_add(app: AppContext, *, slug: str, vendor: str | None) -> None:
    async def action() -> None:
        if vendor is not None:
            record = CompanyRecord(slug=slug, ats_vendor=vendor, ats_override=True)
            await _store_ops(app).upsert_company(record)
            click.echo(f"Added {slug} (vendor: {vendor}, pinned)")
            return
        detected = await _probe_vendor(app, slug)
        now = datetime.now(UTC)
        record = CompanyRecord(
            slug=slug,
            ats_vendor=detected,
            ats_override=False,
            last_verified_at=now,
            last_probe_attempt_at=now,
        )
        await _store_ops(app).upsert_company(record)
        click.echo(f"Added {slug} (vendor: {detected})")

    await run_with_store(app, action)


async def _probe_vendor(app: AppContext, slug: str) -> str:
    """Probe all supported vendors for a slug; raise instead of writing on miss.

    Args:
        app: Initialized application context.
        slug: Company board slug to probe.

    Returns:
        Detected vendor name.

    Raises:
        click.ClickException: When no vendor resolves (definitive miss) or the
            probe outcome is unresolved (network error / ambiguous response).
    """
    # probe_fn resolves the module attribute at call time so tests can
    # monkeypatch ``probe_company`` on this module.
    probe = build_probe_company(app["settings"], probe_fn=probe_company)
    try:
        detected = await probe(slug)
    except (ProbeNetworkError, ProbeIndeterminateError) as exc:
        raise click.ClickException(f"probe unresolved for '{slug}': {exc}") from exc
    if detected is None:
        raise click.ClickException(
            f"no ATS vendor found for '{slug}' "
            f"(tried: {', '.join(_VENDOR_CHOICES)}); not added"
        )
    return detected


# ── list ──────────────────────────────────────────────────────────────


@companies.command(name="list", help="List tracked companies.")
@click.option(
    "--vendor",
    type=click.Choice(_VENDOR_CHOICES),
    default=None,
    help="Filter by ATS vendor.",
)
@click.option(
    "--include-removed",
    is_flag=True,
    help="Include soft-removed companies.",
)
@click.pass_context
def companies_list(
    ctx: click.Context,
    vendor: str | None,
    include_removed: bool,
) -> None:
    """List tracked companies with vendor and failure counters.

    Args:
        ctx: Click invocation context.
        vendor: Optional ATS vendor filter.
        include_removed: Whether to include soft-removed rows.
    """
    app = require_app(ctx)
    asyncio.run(_run_list(app, vendor=vendor, include_removed=include_removed))


async def _run_list(
    app: AppContext,
    *,
    vendor: str | None,
    include_removed: bool,
) -> None:
    async def action() -> None:
        records = await _store_ops(app).list_companies(
            vendor=vendor, include_removed=include_removed
        )
        if not records:
            click.echo("No companies found.")
            return
        slug_width = max(len(rec.slug) for rec in records)
        for rec in records:
            vendor_label = rec.ats_vendor or "unknown"
            click.echo(
                f"{rec.slug:<{slug_width}}  {vendor_label:<10}  "
                f"failures={rec.consecutive_discover_failures}"
            )

    await run_with_store(app, action)


# ── remove ────────────────────────────────────────────────────────────


@companies.command(name="remove", help="Stop tracking a company (soft-remove).")
@click.argument("slug")
@click.pass_context
def companies_remove(ctx: click.Context, slug: str) -> None:
    """Soft-remove a company via the 'removed' vendor sentinel.

    Args:
        ctx: Click invocation context.
        slug: Company board slug.
    """
    app = require_app(ctx)
    asyncio.run(_run_remove(app, slug=slug))


async def _run_remove(app: AppContext, *, slug: str) -> None:
    async def action() -> None:
        was_matched = await _store_ops(app).mark_company_removed(slug)
        if not was_matched:
            raise click.ClickException(
                f"company not tracked (unknown or already removed): {slug}"
            )
        click.echo(f"Removed {slug}")

    await run_with_store(app, action)


__all__ = ["companies"]
