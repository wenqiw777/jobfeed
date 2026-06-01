"""Browser tests for LinkedIn Playwright helpers."""

from __future__ import annotations

import pytest

from jobfeed.adapters.sources._linkedin_discover import discover_linkedin_jobs
from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import QualityBand

pytestmark = pytest.mark.browser

SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords=software"
GOOD_JD = (
    "We build job infrastructure with async Python, structured source adapters, "
    "deterministic tests, PostgreSQL persistence, and careful operational "
    "logging. This role owns reliable scraping boundaries, clean service "
    "orchestration, and production-quality tooling for engineers. You will "
    "design resilient ingestion flows, review source contracts, document edge "
    "cases, and collaborate with product-minded engineers who care about "
    "correctness, observability, and local-first workflows. The team values "
    "strong written communication, thoughtful code review, pragmatic ownership "
    "of ambiguous production behavior, and test fixtures that catch regressions "
    "before source adapters touch live external systems."
)
SEARCH_HTML = f"""
<html>
  <body>
    <ul>
      <li data-occludable-job-id="111">
        <a href="/jobs/view/111/">Fall 2026 Software Engineer Intern</a>
        <div class="job-card-container__primary-description">Northstar</div>
        <div class="job-card-container__metadata-item">Remote</div>
      </li>
      <li data-occludable-job-id="222">
        <a href="/jobs/view/222/">Software Engineer</a>
        <div class="job-card-container__primary-description">Vector</div>
        <div class="job-card-container__metadata-item">New York, NY</div>
      </li>
    </ul>
    <section id="job-details">{GOOD_JD}</section>
  </body>
</html>
"""


class RecordingLogger:
    """Logger double for browser helper tests."""

    def error(self, event: str, **kwargs: object) -> object:
        """Recordable error hook."""
        return (event, kwargs)


async def no_sleep(_seconds: float) -> None:
    """No-op sleeper for deterministic browser tests."""


async def test_linkedin_discover_reads_fixture_cards() -> None:
    """Route-mocked LinkedIn search HTML is harvested into ordered postings."""
    async_api = pytest.importorskip("playwright.async_api")
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()

        async def fulfill(route) -> None:
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=SEARCH_HTML,
            )

        await page.route("**/jobs/search/**", fulfill)
        config = SourcesLinkedInConfig(enabled=True, search_urls=[SEARCH_URL])
        source_urls: dict[str, str] = {}

        try:
            result = await discover_linkedin_jobs(
                page,
                config,
                source_urls,
                no_sleep,
                RecordingLogger(),
            )
        finally:
            await browser.close()

    assert result.needs_reauth is False
    assert [job.canonical_id for job in result.postings] == ["111", "222"]
    assert result.postings[0].jd_quality is QualityBand.GOOD
    assert result.postings[0].enriched_at is not None
    assert source_urls == {"111": SEARCH_URL, "222": SEARCH_URL}
