"""Greenhouse ATS vendor adapter for fetching and probing job boards."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from jobfeed.adapters.sources._http import (
    ATSParseError,
    fetch_json,
    html_to_text,
    probe_url,
)
from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality

_logger = logging.getLogger(__name__)

_VENDOR = "greenhouse"
JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
PROBE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"


async def probe(
    client: httpx.AsyncClient,
    slug: str,
    *,
    timeout: float = 5.0,
) -> bool:
    """HEAD request to board URL.

    Returns True on 2xx, False on definitive 404/410 misses.

    Args:
        client: Shared async HTTP client.
        slug: Company board slug on Greenhouse.
        timeout: Probe-specific timeout in seconds.

    Returns:
        True if the board exists, False if definitively gone.

    Raises:
        ProbeNetworkError: On network-level failures.
        ProbeIndeterminateError: On ambiguous server responses.
    """
    url = PROBE_URL.format(slug=slug)
    return await probe_url(client, url, slug=slug, vendor=_VENDOR, timeout=timeout)


async def fetch_jobs(
    client: httpx.AsyncClient,
    slug: str,
    *,
    discovered_at: datetime,
    timeout: float = 30.0,
) -> list[JobPosting]:
    """GET jobs endpoint, parse each job object into JobPosting.

    Args:
        client: Shared async HTTP client.
        slug: Company board slug on Greenhouse.
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
    jobs_list = _extract_jobs_list(raw, slug)
    postings = []
    for job in jobs_list:
        posting = _parse_job(job, slug, discovered_at)
        if posting is not None:
            postings.append(posting)
    return postings


def _extract_jobs_list(raw: dict[str, Any] | list[Any], slug: str) -> list[Any]:
    """Extract the jobs list from the Greenhouse response envelope.

    Args:
        raw: Parsed JSON response.
        slug: Company slug for error context.

    Returns:
        List of raw job objects.

    Raises:
        ATSParseError: If the response shape is not the expected envelope.
    """
    if not isinstance(raw, dict) or "jobs" not in raw:
        raise ATSParseError(
            f"Greenhouse response missing 'jobs' key for {slug}",
            slug=slug,
            vendor=_VENDOR,
        )
    jobs_list = raw["jobs"]
    if not isinstance(jobs_list, list):
        raise ATSParseError(
            f"Greenhouse 'jobs' is not a list for {slug}",
            slug=slug,
            vendor=_VENDOR,
        )
    return jobs_list


def _parse_job(
    job: Any,
    slug: str,
    discovered_at: datetime,
) -> JobPosting | None:
    """Parse a single Greenhouse job object into a JobPosting.

    Args:
        job: Raw job dict from the API.
        slug: Company slug used as company fallback.
        discovered_at: Scan-start timestamp.

    Returns:
        Parsed JobPosting, or None if the job should be skipped.
    """
    try:
        return _build_posting(job, slug, discovered_at)
    except Exception as exc:
        _logger.warning("Skipping malformed Greenhouse job for %s: %s", slug, exc)
        return None


def _build_posting(
    job: Any,
    slug: str,
    discovered_at: datetime,
) -> JobPosting | None:
    """Build a JobPosting from a validated Greenhouse job object.

    Args:
        job: Raw job dict from the API.
        slug: Company slug used as company fallback.
        discovered_at: Scan-start timestamp.

    Returns:
        Parsed JobPosting, or None if required fields are blank.
    """
    raw_id = job.get("id")
    if raw_id is None:
        _logger.warning("Skipping Greenhouse job with null id for %s", slug)
        return None
    canonical_id = str(raw_id).strip()
    title = _str_field(job, "title")
    url = _str_field(job, "absolute_url")
    jd_text = html_to_text(_str_field(job, "content")).strip()

    if not canonical_id or not title or not url or not jd_text:
        _logger.warning(
            "Skipping Greenhouse job with blank required field for %s", slug
        )
        return None

    company = _str_field(job, "company_name") or slug

    return JobPosting(
        platform=_VENDOR,
        canonical_id=canonical_id,
        url=url,
        title=title,
        company=company,
        location=_extract_location(job),
        discovered_at=discovered_at,
        jd_text=jd_text,
        jd_quality=assess_quality(jd_text),
        posted_at=_parse_updated_at(job.get("updated_at")),
        enriched_at=discovered_at,
        enrich_source="api-greenhouse",
    )


def _str_field(job: Any, key: str) -> str:
    """Extract a string field from a job dict, coercing None to empty."""
    return str(job.get(key) or "").strip()


def _extract_location(job: Any) -> str:
    """Extract location string from a Greenhouse job object.

    Args:
        job: Raw job dict.

    Returns:
        Location string, or empty string if missing or malformed.
    """
    location_obj = job.get("location")
    if not isinstance(location_obj, dict):
        return ""
    return str(location_obj.get("name") or "").strip()


def _parse_updated_at(updated_at: Any) -> datetime | None:
    """Parse Greenhouse updated_at timestamp string.

    Args:
        updated_at: Raw timestamp value from API.

    Returns:
        Parsed datetime, or None if invalid.
    """
    if not updated_at:
        return None
    try:
        return datetime.fromisoformat(str(updated_at))
    except (ValueError, TypeError):
        return None


__all__ = ["JOBS_URL", "PROBE_URL", "fetch_jobs", "probe"]
