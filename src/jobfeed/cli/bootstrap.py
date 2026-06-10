"""Click command seeding tracked companies from public aggregator READMEs.

Fetching, diffing against the store, and writing live here; the pure README
parsing lives in ``adapters/sources/_bootstrap_aggregators.py``.
"""

from __future__ import annotations

import asyncio

import click
import httpx

from jobfeed.adapters.sources._bootstrap_aggregators import (
    BOOTSTRAP_SOURCES,
    extract_ats_slugs,
    extract_ats_slugs_with_age,
)
from jobfeed.adapters.sources._http import create_http_client
from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli.companies import _store_ops
from jobfeed.domain.models import CompanyRecord

_SOURCE_CHOICES = click.Choice([*sorted(BOOTSTRAP_SOURCES), "all"])

# Raw GitHub README fetches are bulk and non-interactive; 15s rides out slow
# CDN responses without hanging the command on a dead source.
_FETCH_TIMEOUT_S = 15.0


@click.command(name="bootstrap-companies", help="Seed companies from README lists.")
@click.option("--source", type=_SOURCE_CHOICES, default="all", help="Source name.")
@click.option("--apply", "apply_changes", is_flag=True, help="Write new companies.")
@click.option("--max-age-days", type=int, help="Skip rows older than N days.")
@click.pass_context
def bootstrap_companies(
    ctx: click.Context,
    source: str,
    apply_changes: bool,
    max_age_days: int | None,
) -> None:
    """Seed ATS slugs from public job-aggregator READMEs (dry-run default).

    Args:
        ctx: Click invocation context.
        source: Bootstrap source name, or 'all' for every source.
        apply_changes: Whether to upsert new companies (else preview only).
        max_age_days: Drop README rows whose age column exceeds this.
    """
    app = require_app(ctx)
    asyncio.run(
        _run_bootstrap(
            app, source=source, apply_changes=apply_changes, max_age_days=max_age_days
        )
    )


async def _run_bootstrap(
    app: AppContext,
    *,
    source: str,
    apply_changes: bool,
    max_age_days: int | None,
) -> None:
    names = sorted(BOOTSTRAP_SOURCES) if source == "all" else [source]
    bodies = await _fetch_sources(names)
    if not bodies:
        raise click.ClickException("all bootstrap sources failed to fetch")
    pairs: set[tuple[str, str]] = set()
    for name, body in bodies.items():
        found = (
            extract_ats_slugs(body)
            if max_age_days is None
            else extract_ats_slugs_with_age(body, max_age_days)
        )
        click.echo(f"{name}: {len(found)} slugs")
        pairs |= found
    await run_with_store(
        app, lambda: _diff_and_write(app, pairs, apply_changes=apply_changes)
    )


async def _fetch_sources(names: list[str]) -> dict[str, str]:
    """Fetch each named README; a failed source is warned about and dropped."""
    client = create_http_client(timeout=_FETCH_TIMEOUT_S)
    try:
        fetched = {name: await _fetch_one(client, name) for name in names}
    finally:
        await client.aclose()
    return {name: body for name, body in fetched.items() if body is not None}


async def _fetch_one(client: httpx.AsyncClient, name: str) -> str | None:
    try:
        response = await client.get(BOOTSTRAP_SOURCES[name])
        response.raise_for_status()
    except httpx.HTTPError as exc:
        click.echo(f"warning: fetch failed for {name}: {exc}", err=True)
        return None
    return response.text


async def _diff_and_write(
    app: AppContext,
    pairs: set[tuple[str, str]],
    *,
    apply_changes: bool,
) -> None:
    """Diff parsed pairs against tracked slugs (removed ones stay skipped)."""
    records = await _store_ops(app).list_companies(include_removed=True)
    existing = {rec.slug for rec in records}
    # A slug listed under two vendors must write once (the store conflict key
    # is slug alone); the alphabetically-first vendor wins deterministically.
    by_slug: dict[str, str] = {}
    for slug, vendor in sorted(pairs):
        by_slug.setdefault(slug, vendor)
    to_add = sorted(item for item in by_slug.items() if item[0] not in existing)
    skipped = len(by_slug) - len(to_add)
    if not apply_changes:
        for slug, vendor in to_add:
            click.echo(f"  would add {slug} ({vendor})")
        click.echo(f"Dry-run: {len(to_add)} new, {skipped} already tracked.")
        return
    for slug, vendor in to_add:
        record = CompanyRecord(slug=slug, ats_vendor=vendor, ats_override=False)
        await _store_ops(app).upsert_company(record)
    click.echo(f"Added {len(to_add)} new, {skipped} already tracked.")


__all__ = ["bootstrap_companies"]
