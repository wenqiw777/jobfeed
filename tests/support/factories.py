"""Shared domain factories for Jobfeed tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality

FIXED_TIME = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
POSTED_TIME = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
ENRICHED_TIME = datetime(2026, 5, 21, 13, 0, tzinfo=UTC)


def fixed_time() -> datetime:
    """Return a stable timezone-aware timestamp for tests.

    Returns:
        Shared deterministic timestamp.
    """
    return FIXED_TIME


def make_job(
    canonical_id: str = "mock-1",
    **overrides: object,
) -> JobPosting:
    """Create a JobPosting fixture with deterministic defaults.

    Args:
        canonical_id: Source-specific natural identity.
        **overrides: Optional named JobPosting field overrides.

    Returns:
        Job posting fixture.
    """
    platform = cast(str, overrides.get("platform", "mock"))
    url = cast(str | None, overrides.get("url"))
    title = cast(str, overrides.get("title", "Backend Intern"))
    company = cast(str, overrides.get("company", "Example"))
    location = cast(str, overrides.get("location", "Remote"))
    discovered_at = cast(datetime | None, overrides.get("discovered_at"))
    jd_text = cast(str | None, overrides.get("jd_text"))
    jd_quality = cast(QualityBand | None, overrides.get("jd_quality"))
    posted_at = cast(datetime | None, overrides.get("posted_at"))
    enriched_at = cast(datetime | None, overrides.get("enriched_at"))
    enrich_source = cast(str | None, overrides.get("enrich_source"))
    if jd_text is not None and jd_quality is None:
        jd_quality = assess_quality(jd_text)
    if jd_text is not None and posted_at is None:
        posted_at = POSTED_TIME
    if jd_text is not None and enriched_at is None:
        enriched_at = ENRICHED_TIME
    if jd_text is not None and enrich_source is None:
        enrich_source = "mock"
    return JobPosting(
        platform=platform,
        canonical_id=canonical_id,
        url=url or f"https://example.com/{canonical_id}",
        title=title,
        company=company,
        location=location,
        discovered_at=discovered_at or FIXED_TIME,
        jd_text=jd_text,
        jd_quality=jd_quality,
        posted_at=posted_at,
        enriched_at=enriched_at,
        enrich_source=enrich_source,
    )


__all__ = ["fixed_time", "make_job"]
