"""DTOs for the companies routes: tracked ATS companies CRUD + vendor probe."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from jobfeed.domain.company_slug import is_valid_slug, normalize_slug
from jobfeed.domain.models import CompanyRecord

# The three real ATS vendors; the soft-removal sentinel is not a vendor.
VendorName = Literal["greenhouse", "ashby", "lever"]

# Soft removal stores this sentinel in ats_vendor (there is no boolean
# column); the wire shape derives the removed flag from it.
REMOVED_SENTINEL = "removed"

# Bounds one probe batch (each entry can fan out to three vendor probes);
# larger pastes must be split client-side.
MAX_PROBE_ENTRIES = 200


def _normalized_slug(value: str) -> str:
    """Trim+lowercase a slug field; reject text outside the slug charset.

    Normalizing here keeps the TEXT primary key canonical (" Acme " and
    "acme" must be the same row).
    """
    slug = normalize_slug(value)
    if not is_valid_slug(slug):
        raise ValueError(f"not a valid company slug: {value!r}")
    return slug


class CompanyOut(BaseModel):
    """One tracked company row of the wire contract."""

    slug: str
    vendor: str | None
    consecutive_discover_failures: int
    removed: bool


class CompaniesListResponse(BaseModel):
    """``GET /api/companies`` response."""

    companies: list[CompanyOut]


class CompanyAddBody(BaseModel):
    """``POST /api/companies`` request body (the vendor is pinned)."""

    slug: str
    vendor: VendorName

    _slug = field_validator("slug")(_normalized_slug)


class BulkCompanyRow(BaseModel):
    """One company of a bulk-insert request."""

    slug: str
    vendor: VendorName

    _slug = field_validator("slug")(_normalized_slug)


class CompaniesBulkBody(BaseModel):
    """``POST /api/companies/bulk`` request body."""

    rows: list[BulkCompanyRow] = Field(min_length=1)


class BulkInsertResponse(BaseModel):
    """Bulk outcome: rows upserted (new + restored; active slugs skipped)."""

    inserted: int


class ProbeBody(BaseModel):
    """``POST /api/companies/probe`` request body (1..200 entries)."""

    entries: list[str] = Field(min_length=1, max_length=MAX_PROBE_ENTRIES)


class ProbeEntryResult(BaseModel):
    """Per-entry probe outcome.

    A definitive all-vendor miss is ``vendor=None, error=None``; a
    normalization or transport failure carries ``error`` instead.
    """

    input: str
    slug: str | None
    vendor: str | None
    error: str | None


class ProbeResponse(BaseModel):
    """``POST /api/companies/probe`` response, in request order."""

    results: list[ProbeEntryResult]


def company_out(record: CompanyRecord) -> CompanyOut:
    """Render a domain company record as the wire row.

    Soft removal overwrites ``ats_vendor`` with the ``removed`` sentinel (the
    original vendor is not retained), so the wire shape derives ``removed``
    from the sentinel and reports ``vendor`` as None for removed rows.

    Args:
        record: Domain company record.

    Returns:
        Wire-shape company row.
    """
    is_removed = record.ats_vendor == REMOVED_SENTINEL
    return CompanyOut(
        slug=record.slug,
        vendor=None if is_removed else record.ats_vendor,
        consecutive_discover_failures=record.consecutive_discover_failures,
        removed=is_removed,
    )


__all__ = [
    "MAX_PROBE_ENTRIES",
    "REMOVED_SENTINEL",
    "BulkCompanyRow",
    "BulkInsertResponse",
    "CompaniesBulkBody",
    "CompaniesListResponse",
    "CompanyAddBody",
    "CompanyOut",
    "ProbeBody",
    "ProbeEntryResult",
    "ProbeResponse",
    "VendorName",
    "company_out",
]
