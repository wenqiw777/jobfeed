"""Deterministic mock job source for Phase 0 tests and local smoke runs."""

from __future__ import annotations

from datetime import datetime

from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality

DEFAULT_JOB_COUNT = 3
DISCOVERED_AT = datetime.fromisoformat("2026-05-21T12:00:00+00:00")
MOCK_TITLES = [
    "Backend Platform Intern",
    "AI Workflow Engineer",
    "Data Automation Intern",
]
MOCK_COMPANIES = [
    "Northstar Systems",
    "Beacon Labs",
    "Evergreen Data",
]
MOCK_JD_TEXT = (
    "This role builds local-first automation for job discovery, evaluation, "
    "and candidate workflow operations. The team needs someone comfortable "
    "with Python services, structured data modeling, async execution, and "
    "clear operational tooling. You will work on ingestion from multiple "
    "sources, normalize job descriptions, write deterministic tests around "
    "edge cases, and keep observability simple enough for daily use. Strong "
    "candidates can explain tradeoffs, preserve user data, and design small "
    "interfaces between adapters and domain logic. Experience with SQLite, "
    "CLI tooling, Docker-based developer workflows, and LLM prompt contracts "
    "is useful. The work favors maintainable code, precise error handling, "
    "and readable documentation over broad framework usage."
)


class MockSource:
    """SimpleSource implementation that returns canned realistic postings."""

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:
        """Fetch deterministic mock jobs.

        Args:
            config: Source config. Optional `count` controls result size.

        Returns:
            Mock job postings with stable identities and long JD text.
        """
        count = _config_count(config)
        return [_make_job(index) for index in range(count)]


def _config_count(config: dict[str, object]) -> int:
    value = config.get("count", DEFAULT_JOB_COUNT)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("mock source count must be an integer")
    return max(value, 0)


def _make_job(index: int) -> JobPosting:
    title = MOCK_TITLES[index % len(MOCK_TITLES)]
    company = MOCK_COMPANIES[index % len(MOCK_COMPANIES)]
    return JobPosting(
        platform="mock",
        canonical_id=f"mock-{index + 1}",
        url=f"https://example.com/jobs/mock-{index + 1}",
        title=title,
        company=company,
        location="Remote",
        discovered_at=DISCOVERED_AT,
        jd_text=MOCK_JD_TEXT,
        jd_quality=assess_quality(MOCK_JD_TEXT),
        enrich_source="mock",
    )


__all__ = ["MOCK_JD_TEXT", "MockSource"]
