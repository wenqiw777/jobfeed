"""Companies routes: tracked ATS companies CRUD, bulk insert, vendor probe.

Thin shell over the store's company ops plus the injected per-slug vendor
probe. The probe callable is assembled by the cli composition root and
reaches the routes via ``app.state``, so this module never imports
``jobfeed.adapters`` (architecture boundary). The probe endpoint performs
no store writes.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, cast

from fastapi import APIRouter, Depends

from jobfeed.domain.company_slug import (
    is_valid_slug,
    normalize_company_entry,
    normalize_slug,
)
from jobfeed.domain.models import CompanyRecord
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.web.deps import ProbeVendorFn, get_probe_company, get_store
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas import (
    REMOVED_SENTINEL,
    BulkCompanyRow,
    BulkInsertResponse,
    CompaniesBulkBody,
    CompaniesListResponse,
    CompanyAddBody,
    CompanyOut,
    OkResponse,
    ProbeBody,
    ProbeEntryResult,
    ProbeResponse,
    VendorName,
    company_out,
)

_HTTP_NOT_FOUND = 404
_HTTP_VALIDATION_ERROR = 422
# Bound on concurrent outbound probes within one request batch.
_PROBE_CONCURRENCY = 5

router = APIRouter()

_Store = Annotated[JobStore, Depends(get_store)]
_Probe = Annotated[ProbeVendorFn, Depends(get_probe_company)]


def _ops(store: JobStore) -> StoreOpsMixin:
    """Narrow the store to its company-ops capability."""
    return cast(StoreOpsMixin, store)


@router.get("/companies")
async def list_companies(
    store: _Store,
    vendor: VendorName | None = None,
    include_removed: bool = False,
) -> CompaniesListResponse:
    """List tracked companies, slug-ascending.

    Args:
        store: Shared job store from the app state.
        vendor: Optional ATS vendor filter.
        include_removed: Whether soft-removed rows are included.

    Returns:
        Company rows with vendor, failure count, and the derived removed flag.
    """
    records = await _ops(store).list_companies(
        vendor=vendor, include_removed=include_removed
    )
    return CompaniesListResponse(companies=[company_out(r) for r in records])


@router.post("/companies")
async def add_company(body: CompanyAddBody, store: _Store) -> CompanyOut:
    """Track a company with a pinned vendor (upsert; never re-probed).

    The upsert's ON CONFLICT path also restores a soft-removed slug: the new
    vendor overwrites the removal sentinel.

    Args:
        body: Slug and pinned ATS vendor.
        store: Shared job store from the app state.

    Returns:
        The written row (vendor pinned, failure counter reset to zero).
    """
    record = CompanyRecord(slug=body.slug, ats_vendor=body.vendor, ats_override=True)
    await _ops(store).upsert_company(record)
    return company_out(record)


@router.post("/companies/bulk")
async def bulk_add_companies(
    body: CompaniesBulkBody, store: _Store
) -> BulkInsertResponse:
    """Upsert many companies at once, skipping slugs that are already active.

    Payload duplicates collapse to their first occurrence. Slugs tracked and
    not removed are skipped; everything else — new or soft-removed — goes
    through the same upsert path as the single POST, so a removed company in
    the payload is restored. Bulk rows write ``ats_override=False``
    (mirroring ``bootstrap-companies``) so bulk-added vendors stay
    re-probeable.

    Args:
        body: Bulk rows (slug + vendor each).
        store: Shared job store from the app state.

    Returns:
        Count of rows upserted (new plus restored).
    """
    records = await _ops(store).list_companies(include_removed=True)
    active = {r.slug for r in records if r.ats_vendor != REMOVED_SENTINEL}
    to_write = _rows_to_upsert(body.rows, active)
    for slug, vendor in to_write:
        record = CompanyRecord(slug=slug, ats_vendor=vendor, ats_override=False)
        await _ops(store).upsert_company(record)
    return BulkInsertResponse(inserted=len(to_write))


def _rows_to_upsert(
    rows: list[BulkCompanyRow], active: set[str]
) -> list[tuple[str, str]]:
    """Collapse payload duplicates (first wins) and drop active slugs.

    Args:
        rows: Requested bulk rows (slugs already normalized by the schema).
        active: Slugs currently tracked and not soft-removed.

    Returns:
        ``(slug, vendor)`` pairs to upsert: new or to-be-restored companies.
    """
    seen = set(active)
    kept: list[tuple[str, str]] = []
    for row in rows:
        if row.slug in seen:
            continue
        seen.add(row.slug)
        kept.append((row.slug, row.vendor))
    return kept


@router.delete("/companies/{slug}")
async def remove_company(slug: str, store: _Store) -> OkResponse:
    """Stop tracking a company (soft remove via the vendor sentinel).

    The path slug is normalized (trim + lowercase) before the store call,
    matching the write endpoints' slug handling.

    Args:
        slug: Company board slug.
        store: Shared job store from the app state.

    Returns:
        Acknowledgement.

    Raises:
        ApiError: 422 for text outside the slug charset; 404 when the slug
            is unknown or already removed.
    """
    normalized = normalize_slug(slug)
    if not is_valid_slug(normalized):
        raise ApiError(
            _HTTP_VALIDATION_ERROR,
            "validation_error",
            f"not a valid company slug: {slug!r}",
        )
    was_matched = await _ops(store).mark_company_removed(normalized)
    if not was_matched:
        raise ApiError(
            _HTTP_NOT_FOUND,
            "not_found",
            f"company not tracked (unknown or already removed): {normalized}",
        )
    return OkResponse()


@router.post("/companies/probe")
async def probe_companies(body: ProbeBody, probe: _Probe) -> ProbeResponse:
    """Resolve each pasted entry's ATS vendor without writing anything.

    Entries are normalized to candidate slugs (lowercase trim; vendor board
    URLs reduce to their slug), then probed under a per-request concurrency
    bound of 5. One entry's failure never aborts the batch.

    Args:
        body: Pasted entries (1..200).
        probe: Injected per-slug vendor probe from the cli assembly.

    Returns:
        Per-entry results in request order.
    """
    semaphore = asyncio.Semaphore(_PROBE_CONCURRENCY)
    results = await asyncio.gather(
        *(_probe_entry(entry, probe, semaphore) for entry in body.entries)
    )
    return ProbeResponse(results=list(results))


async def _probe_entry(
    entry: str, probe: ProbeVendorFn, semaphore: asyncio.Semaphore
) -> ProbeEntryResult:
    """Normalize and probe one pasted entry, never raising.

    Args:
        entry: Raw pasted entry.
        probe: Injected per-slug vendor probe.
        semaphore: Shared bound on concurrent outbound probes.

    Returns:
        Per-entry outcome: a vendor hit, a definitive miss (vendor and error
        both None), or an error string (normalization or transport failure).
    """
    candidate = normalize_company_entry(entry)
    if candidate.slug is None:
        return ProbeEntryResult(
            input=entry, slug=None, vendor=None, error=candidate.error
        )
    try:
        async with semaphore:
            vendor = await probe(candidate.slug)
    # Broad on purpose: per-entry isolation, and the probe's error types
    # (ProbeNetworkError / ProbeIndeterminateError) live in the adapters
    # layer, which web modules must not import.
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        return ProbeEntryResult(
            input=entry, slug=candidate.slug, vendor=None, error=message
        )
    return ProbeEntryResult(input=entry, slug=candidate.slug, vendor=vendor, error=None)


__all__ = ["router"]
