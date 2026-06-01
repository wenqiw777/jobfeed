"""Unit tests for the LinkedIn scan-session enrichment tiers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

import jobfeed.adapters.sources._linkedin_enrich as enrich_module
from jobfeed.adapters.sources._linkedin_enrich import LinkedInScanSession
from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import JobPosting, QualityBand

SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords=swe"
# Long enough (>500 chars) to assess as GOOD so tier1 short-circuits enrich().
GOOD_JD = (
    "We build job-search infrastructure with async Python, structured source "
    "adapters, deterministic tests, PostgreSQL persistence, and careful "
    "operational logging. This role owns reliable scraping boundaries, clean "
    "service orchestration, and production-quality tooling for engineers who "
    "care about correctness, observability, and pragmatic ownership of ambiguous "
    "production behavior across the whole ingestion pipeline end to end. You will "
    "design resilient discovery flows, review source contracts, document edge "
    "cases, harden error handling, and collaborate with product-minded engineers "
    "who value strong written communication, thoughtful code review, and test "
    "fixtures that catch regressions before adapters ever touch live systems."
)


class _Logger:
    def error(self, _event: str, **_kwargs: object) -> None:
        """Accept error logs."""


async def _no_sleep(_seconds: float) -> None:
    """No-op sleeper."""


class _FakePage:
    """Records goto URLs so tier routing can be asserted."""

    def __init__(self) -> None:
        self.goto_urls: list[str] = []

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.goto_urls.append(url)


def _session(page: _FakePage, *, tier2_cap: int = 30) -> LinkedInScanSession:
    config = SourcesLinkedInConfig(tier2_cap=tier2_cap)
    return LinkedInScanSession(
        page=page, config=config, sleeper=_no_sleep, logger=_Logger()
    )


def _posting(canonical_id: str) -> JobPosting:
    return JobPosting(
        platform="linkedin",
        canonical_id=canonical_id,
        url=f"https://www.linkedin.com/jobs/view/{canonical_id}/",
        title="Backend Intern",
        company="Northstar Systems",
        location="Remote",
        discovered_at=datetime.now(UTC),
        jd_text=None,
        jd_quality=QualityBand.MISSING,
    )


@pytest.mark.asyncio
async def test_tier1_selects_job_via_current_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier1 must reopen the search with currentJobId so the right pane loads."""
    page = _FakePage()

    async def fake_read(_page: object) -> str:
        return GOOD_JD if "currentJobId=li-1" in page.goto_urls[-1] else ""

    monkeypatch.setattr(enrich_module, "read_job_description", fake_read)
    session = _session(page)
    session.source_search_urls["li-1"] = SEARCH_URL

    result = await session.enrich(_posting("li-1"))

    assert "currentJobId=li-1" in page.goto_urls[-1]
    assert result.enrich_source == "linkedin_search_pane"
    assert result.jd_text == GOOD_JD
    assert result.error is None


@pytest.mark.asyncio
async def test_tier2_used_when_no_search_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without provenance, tier1 is skipped and tier2 hits the detail page."""
    page = _FakePage()

    async def fake_read(_page: object) -> str:
        return GOOD_JD

    monkeypatch.setattr(enrich_module, "read_job_description", fake_read)
    session = _session(page)
    posting = _posting("li-2")

    result = await session.enrich(posting)

    assert page.goto_urls == [posting.url]
    assert result.enrich_source == "linkedin_detail"


@pytest.mark.asyncio
async def test_tier2_cap_blocks_further_detail_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the tier2 cap is reached, further postings return a cap error."""
    page = _FakePage()

    async def fake_read(_page: object) -> str:
        return ""  # force tier2 to be exercised but yield no JD

    monkeypatch.setattr(enrich_module, "read_job_description", fake_read)
    session = _session(page, tier2_cap=1)

    first = await session.enrich(_posting("a"))
    second = await session.enrich(_posting("b"))

    assert "detail JD missing" in (first.error or "")
    assert "tier2 cap reached" in (second.error or "")
    assert page.goto_urls == ["https://www.linkedin.com/jobs/view/a/"]


@pytest.mark.asyncio
async def test_fresh_posting_short_circuits_without_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GOOD-quality, already-enriched posting is returned without any nav."""
    page = _FakePage()

    async def fake_read(_page: object) -> str:  # pragma: no cover - must not run
        raise AssertionError("fresh posting should not read the page")

    monkeypatch.setattr(enrich_module, "read_job_description", fake_read)
    session = _session(page)
    fresh = replace(
        _posting("li-fresh"),
        jd_text=GOOD_JD,
        jd_quality=QualityBand.GOOD,
        enriched_at=datetime.now(UTC),
    )

    result = await session.enrich(fresh)

    assert page.goto_urls == []
    assert result.enrich_source == "cached-fresh"
    assert result.jd_text == GOOD_JD
