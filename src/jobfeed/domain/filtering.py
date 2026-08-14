"""Deterministic hard filters for source job postings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from jobfeed.domain.models import JobPosting

_UNITED_STATES_ALLOWLIST_VALUE = "united states"
_US_NAMES = ("united states", "u.s.", "usa")
_US_STATE_CODES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    ]
)


@dataclass(kw_only=True)
class HardFilters:
    """Hard filter configuration with company, location, and date rules.

    title_blocklist is kept for back-compat deserialization only — it is
    intentionally NOT applied by apply_hard_filters. Seniority/title filtering
    moved to the ML gate.
    """

    title_blocklist: list[str] = field(default_factory=list)
    company_blocklist: list[str] = field(default_factory=list)
    location_allowlist: list[str] = field(default_factory=list)
    location_blocklist: list[str] = field(default_factory=list)
    posted_within_days: int | None = None
    big_company_list: list[str] = field(default_factory=list)
    big_company_days: int = 90


def apply_hard_filters(
    job: JobPosting,
    filters: HardFilters,
    *,
    now: datetime | None = None,
) -> str | None:
    """Apply hard filters to one job posting.

    Order: company blocklist → location allow/block → freshness.
    title_blocklist is intentionally not checked here; it moved to the ML gate.

    Args:
        job: Job posting to inspect.
        filters: Filter configuration.
        now: Reference timestamp for freshness checks (injectable for tests;
             defaults to UTC wall-clock when omitted).

    Returns:
        None when the job passes; otherwise a human-readable filter reason.
    """
    reason = _company_reason(job.company, filters.company_blocklist)
    if reason is not None:
        return reason

    reason = _location_reason(
        job.location, filters.location_allowlist, filters.location_blocklist
    )
    if reason is not None:
        return reason

    reason = _freshness_reason(job.company, job.discovered_at, filters, now)
    if reason is not None:
        return reason

    return None


# ---------------------------------------------------------------------------
# Stage helpers — each returns a reason string or None
# ---------------------------------------------------------------------------


def _company_reason(company: str, blocklist: list[str]) -> str | None:
    match = _first_match(company, blocklist)
    if match is not None:
        return f'company contains "{match}"'
    return None


def _location_reason(
    location: str | None,
    allowlist: list[str],
    blocklist: list[str],
) -> str | None:
    """Return a block reason for location, or None.

    Empty/None location passes unconditionally (safety valve: we never block
    a job just because we failed to capture its location).
    """
    if not location:
        return None

    if allowlist and not _matches_location_allowlist(location, allowlist):
        return "location not in allowlist"

    match = _first_match(location, blocklist)
    if match is not None:
        return f'location contains "{match}"'

    return None


def _matches_location_allowlist(location: str, allowlist: list[str]) -> bool:
    if _any_match(location, allowlist):
        return True
    has_us_filter = any(
        value.casefold() == _UNITED_STATES_ALLOWLIST_VALUE for value in allowlist
    )
    if not has_us_filter:
        return False
    normalized = location.casefold()
    if any(name in normalized for name in _US_NAMES):
        return True
    tokens = re.findall(r"\b[A-Z]{2}\b", location.upper().replace(".", ""))
    return any(token in _US_STATE_CODES for token in tokens)


def _freshness_reason(
    company: str,
    discovered_at: datetime,
    filters: HardFilters,
    now: datetime | None,
) -> str | None:
    """Return a staleness reason, or None.

    Uses big_company_days for companies in big_company_list; otherwise uses
    posted_within_days. Returns None when no day limit is configured.
    """
    if filters.posted_within_days is None:
        return None

    is_big = bool(
        filters.big_company_list and _any_match(company, filters.big_company_list)
    )
    days_limit = filters.big_company_days if is_big else filters.posted_within_days

    if _is_stale(discovered_at, days_limit, now):
        return f"older than {days_limit} days"

    return None


def _is_stale(discovered_at: datetime, days_limit: int, now: datetime | None) -> bool:
    """Return True if discovered_at is older than days_limit."""
    reference = now or datetime.now(UTC)
    if discovered_at.tzinfo is None:
        discovered_at = discovered_at.replace(tzinfo=UTC)
    cutoff = reference - timedelta(days=days_limit)
    return discovered_at < cutoff


# ---------------------------------------------------------------------------
# Substring matching helpers
# ---------------------------------------------------------------------------


def _first_match(value: str, candidates: list[str]) -> str | None:
    """Return the first candidate that appears as a substring of value (case-fold)."""
    normalized = value.casefold()
    for item in candidates:
        if item and item.casefold() in normalized:
            return item
    return None


def _any_match(value: str, candidates: list[str]) -> bool:
    """Return True if any candidate appears as a substring of value (case-fold)."""
    normalized = value.casefold()
    return any(item and item.casefold() in normalized for item in candidates)


__all__ = ["HardFilters", "apply_hard_filters"]
