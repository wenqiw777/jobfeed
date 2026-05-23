"""Lever ATS vendor adapter for fetching and probing job boards."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from jobfeed.adapters.sources._http import (
    ATSFetchError,
    ATSParseError,
    ProbeIndeterminateError,
    ProbeNetworkError,
    fetch_json,
    html_to_text,
)
from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality

_logger = logging.getLogger(__name__)

_VENDOR = "lever"
JOBS_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


async def probe(
    client: httpx.AsyncClient,
    slug: str,
    *,
    timeout: float = 5.0,
) -> bool:
    """GET jobs endpoint to probe board existence.

    Returns True on 2xx with parseable JSON, False on definitive 404/410.
    Raises ProbeIndeterminateError on 2xx with invalid or non-list JSON.

    Args:
        client: Shared async HTTP client.
        slug: Company board slug on Lever.
        timeout: Probe-specific timeout in seconds.

    Returns:
        True if the board is live and JSON is valid, False if definitively gone.

    Raises:
        ProbeNetworkError: On network-level failures.
        ProbeIndeterminateError: On ambiguous responses or 2xx with invalid JSON.
    """
    url = JOBS_URL.format(slug=slug)
    try:
        raw = await fetch_json(client, url, slug=slug, vendor=_VENDOR, timeout=timeout)
    except ATSParseError as exc:
        raise ProbeIndeterminateError(
            f"Lever probe 2xx but JSON parse failed for {slug}: {exc}",
            slug=slug,
            vendor=_VENDOR,
        ) from exc
    except ATSFetchError as exc:
        if exc.status_code in {404, 410}:
            return False
        if exc.status_code is None:
            raise ProbeNetworkError(str(exc), slug=slug, vendor=_VENDOR) from exc
        raise ProbeIndeterminateError(
            str(exc), slug=slug, vendor=_VENDOR, status_code=exc.status_code
        ) from exc

    if not isinstance(raw, list):
        raise ProbeIndeterminateError(
            f"Lever probe 2xx but response is not a list for {slug}",
            slug=slug,
            vendor=_VENDOR,
        )
    return True


async def fetch_jobs(
    client: httpx.AsyncClient,
    slug: str,
    *,
    discovered_at: datetime,
    timeout: float = 30.0,
) -> list[JobPosting]:
    """GET jobs endpoint (response is top-level array, no envelope).

    Args:
        client: Shared async HTTP client.
        slug: Company board slug on Lever.
        discovered_at: Scan-start timestamp to stamp on each posting.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed job postings from the board.

    Raises:
        ATSFetchError: On HTTP or network failures.
        ATSParseError: On malformed top-level response shape.
    """
    url = JOBS_URL.format(slug=slug)
    raw = await fetch_json(client, url, slug=slug, vendor=_VENDOR, timeout=timeout)
    if not isinstance(raw, list):
        raise ATSParseError(
            f"Lever response is not a list for {slug}",
            slug=slug,
            vendor=_VENDOR,
        )
    postings = []
    for job in raw:
        posting = _parse_job(job, slug, discovered_at)
        if posting is not None:
            postings.append(posting)
    return postings


def _parse_job(
    job: Any,
    slug: str,
    discovered_at: datetime,
) -> JobPosting | None:
    """Parse a single Lever job object into a JobPosting.

    Args:
        job: Raw job dict from the API.
        slug: Company slug used as company name.
        discovered_at: Scan-start timestamp.

    Returns:
        Parsed JobPosting, or None if the job should be skipped.
    """
    try:
        return _build_posting(job, slug, discovered_at)
    except Exception as exc:
        _logger.warning("Skipping malformed Lever job for %s: %s", slug, exc)
        return None


def _build_posting(
    job: Any,
    slug: str,
    discovered_at: datetime,
) -> JobPosting | None:
    """Build a JobPosting from a validated Lever job object.

    Args:
        job: Raw job dict from the API.
        slug: Company slug used as company name.
        discovered_at: Scan-start timestamp.

    Returns:
        Parsed JobPosting, or None if required fields are blank.
    """
    raw_id = job.get("id")
    if raw_id is None:
        _logger.warning("Skipping Lever job with null id for %s", slug)
        return None
    canonical_id = str(raw_id).strip()

    title = str(job.get("text") or "").strip()
    url = str(job.get("hostedUrl") or "").strip()
    jd_text = _extract_jd_text(job)

    if not canonical_id or not title or not url or not jd_text:
        _logger.warning("Skipping Lever job with blank required field for %s", slug)
        return None

    location = _extract_location(job)
    posted_at = _parse_created_at(job.get("createdAt"))

    return JobPosting(
        platform=_VENDOR,
        canonical_id=canonical_id,
        url=url,
        title=title,
        company=slug,
        location=location,
        discovered_at=discovered_at,
        jd_text=jd_text,
        jd_quality=assess_quality(jd_text),
        posted_at=posted_at,
        enriched_at=discovered_at,
        enrich_source="api-lever",
    )


def _extract_jd_text(job: Any) -> str:
    """Extract job description text from a Lever job object.

    Combines descriptionPlain and lists content via html_to_text for HTML segments.

    Args:
        job: Raw job dict.

    Returns:
        Plain-text job description, stripped.
    """
    parts: list[str] = []
    plain = job.get("descriptionPlain") or ""
    if plain:
        parts.append(str(plain).strip())

    lists = job.get("lists") or []
    for item in lists:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or ""
        if content:
            parts.append(html_to_text(str(content)).strip())

    return "\n".join(p for p in parts if p)


def _extract_location(job: Any) -> str:
    """Extract location string from a Lever job object.

    Lever stores location under categories.location.

    Args:
        job: Raw job dict.

    Returns:
        Location string, or empty string if missing or malformed.
    """
    categories = job.get("categories")
    if not isinstance(categories, dict):
        return ""
    return str(categories.get("location") or "").strip()


def _parse_created_at(created_at: Any) -> datetime | None:
    """Parse Lever createdAt Unix millisecond timestamp.

    Args:
        created_at: Raw timestamp value from API (integer milliseconds).

    Returns:
        Parsed datetime with UTC timezone, or None if invalid.
    """
    if created_at is None:
        return None
    try:
        return datetime.fromtimestamp(int(created_at) / 1000, tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


__all__ = ["JOBS_URL", "fetch_jobs", "probe"]
