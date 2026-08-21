"""Unit tests for LinkedIn Playwright discovery helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import jobfeed.adapters.sources._linkedin_discover as discover_module
from jobfeed.adapters.sources._linkedin_discover import (
    build_search_specs,
    discover_linkedin_jobs,
    order_discovered_postings,
)
from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import JobPosting

DEFAULT_MAX_JOBS = 20
URL_MAX_JOBS = 5
GROUP_MAX_JOBS = 2


class _RecordingLogger:
    def error(self, _event: str, **_kwargs: object) -> None:
        """Accept error logs."""


async def _no_sleep(_seconds: float) -> None:
    """No-op sleeper."""


class _AuthwallMouse:
    async def wheel(self, _x: int, _y: int) -> None:
        """No-op scroll."""


class _AuthwallPage:
    """Page stand-in whose URL lands on the LinkedIn login wall."""

    url = "https://www.linkedin.com/login"
    mouse = _AuthwallMouse()

    async def goto(self, _url: str, **_kwargs: object) -> None:
        """Accept navigation."""


def test_build_search_specs_accepts_plain_and_structured_urls() -> None:
    """Search specs should preserve per-URL and per-group limits."""
    config = SourcesLinkedInConfig(
        enabled=True,
        max_jobs=DEFAULT_MAX_JOBS,
        search_urls=[
            "https://linkedin.test/jobs?keywords=swe",
            {
                "url": "https://linkedin.test/jobs?keywords=fall+intern",
                "max_jobs": URL_MAX_JOBS,
                "group": "fall-intern",
                "group_max_jobs": GROUP_MAX_JOBS,
            },
        ],
    )

    specs = build_search_specs(config)

    assert specs[0].url == "https://linkedin.test/jobs?keywords=swe"
    assert specs[0].max_jobs == DEFAULT_MAX_JOBS
    assert specs[0].group is None
    assert specs[1].url == "https://linkedin.test/jobs?keywords=fall+intern"
    assert specs[1].max_jobs == URL_MAX_JOBS
    assert specs[1].group == "fall-intern"
    assert specs[1].group_max_jobs == GROUP_MAX_JOBS


def test_authenticated_linkedin_uses_one_total_across_searches() -> None:
    """A later search cannot exceed the source-level total job budget."""
    state = discover_module._DiscoverState(
        postings=[_posting("one", "SWE"), _posting("two", "Backend Engineer")],
        seen={"one", "two"},
        group_counts={},
        source_search_urls={},
        source_max_jobs=2,
    )
    spec = build_search_specs(
        SourcesLinkedInConfig(
            enabled=True,
            max_jobs=2,
            search_urls=["https://linkedin.test/jobs?keywords=second"],
        )
    )[0]

    assert discover_module._can_accept(spec, 0, state) is False


def test_order_discovered_postings_prioritizes_fall_interns() -> None:
    """LinkedIn discovery output should be intern-first before ScanService sees it."""
    rest = _posting("rest", "Software Engineer")
    intern = _posting("intern", "Backend Intern")
    fall_by_url = _posting("fall-url", "Software Engineer Intern")
    fall_by_title = _posting("fall-title", "Fall 2026 Software Engineer Intern")
    source_urls = {
        "rest": "https://linkedin.test/search?x=1",
        "intern": "https://linkedin.test/search?x=2",
        "fall-url": "https://linkedin.test/search?keywords=fall",
        "fall-title": "https://linkedin.test/search?x=4",
    }

    ordered = order_discovered_postings(
        [rest, intern, fall_by_url, fall_by_title],
        source_urls,
    )

    assert [posting.canonical_id for posting in ordered] == [
        "fall-url",
        "fall-title",
        "intern",
        "rest",
    ]


def test_order_discovered_postings_does_not_prioritize_year_literal() -> None:
    """Recruiting-cycle years are config/search terms, not ranking literals."""
    intern = _posting("intern", "Backend Intern")
    year_only = _posting("year-only", "Summer 2026 Software Engineer Intern")

    ordered = order_discovered_postings(
        [year_only, intern],
        {
            "year-only": "https://linkedin.test/search?keywords=summer",
            "intern": "https://linkedin.test/search?keywords=backend",
        },
    )

    assert [posting.canonical_id for posting in ordered] == ["intern", "year-only"]


@pytest.mark.asyncio
async def test_discover_signals_reauth_via_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authwall is surfaced through DiscoverResult, not a page side channel."""

    async def fake_body(_page: object) -> str:
        return "Please sign in to continue"

    monkeypatch.setattr(discover_module, "read_body_text", fake_body)
    page = _AuthwallPage()
    config = SourcesLinkedInConfig(
        enabled=True,
        search_urls=["https://www.linkedin.com/jobs/search/?keywords=swe"],
    )

    result = await discover_linkedin_jobs(
        page, config, {}, _no_sleep, _RecordingLogger()
    )

    assert result.needs_reauth is True
    assert result.postings == []
    assert not hasattr(page, "_jobfeed_needs_reauth")


@pytest.mark.asyncio
async def test_scroll_results_makes_multiple_paced_passes() -> None:
    """Discovery scrolls several paced passes so LinkedIn's lazy cards render."""

    class _CountingMouse:
        def __init__(self) -> None:
            self.wheels = 0

        async def wheel(self, _x: int, _y: int) -> None:
            self.wheels += 1

    class _CountingPage:
        def __init__(self) -> None:
            self.mouse = _CountingMouse()

    page = _CountingPage()
    await discover_module._scroll_results(page, _no_sleep)

    assert page.mouse.wheels == discover_module._SCROLL_PASSES


def _posting(canonical_id: str, title: str) -> JobPosting:
    return JobPosting(
        platform="linkedin",
        canonical_id=canonical_id,
        url=f"https://linkedin.test/jobs/view/{canonical_id}",
        title=title,
        company="Northstar Systems",
        location="Remote",
        discovered_at=datetime.now(UTC),
    )
