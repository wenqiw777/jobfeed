"""Live smoke tests for the Phase 4a sources (real HTTP / real JobSpy scrapes).

Never run in CI and excluded from the default suite (the ``live`` marker is in
``addopts``'s exclusion). Run manually with::

    pytest -m live -o "addopts=" tests/live/test_phase4_live_smoke.py

They verify the real integrations still parse today's upstream shapes:
SpeedyApply against the live GitHub README + whatever ATS each row routes to,
and the Indeed JobSpy source against a small real query. JobSpy hits anti-bot'd
endpoints — an empty result or a contained ``JobSpyError`` usually means
"blocked today", not an adapter regression; re-run later.

The LinkedIn Playwright SessionSource live smoke is Phase 4b (browser), not here.
"""

from __future__ import annotations

import pytest

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.config import (
    SourcesIndeedConfig,
    SourcesSpeedyApplyConfig,
)
from jobfeed.observability import get_logger

pytestmark = pytest.mark.live

_MIN_JD_LENGTH = 100
_JOBSPY_MAX = 5

_INDEED_QUERY = "https://www.indeed.com/jobs?q=software+engineer+intern&l=Remote"
_SPEEDYAPPLY_README = (
    "https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/README.md"
)


class TestSpeedyApplyLiveSmoke:
    """Hits the real speedyapply README and routes rows to live ATS boards."""

    async def test_speedyapply_live(self) -> None:
        """Fetch the real README, parse rows, and route at least one JD."""
        config = SourcesSpeedyApplyConfig(
            enabled=True, search_urls=[_SPEEDYAPPLY_README]
        )
        async with create_http_client() as client:
            source = SpeedyApplySource(
                client=client, config=config, logger=get_logger()
            )
            postings = await source.fetch_jobs({})

        assert len(postings) > 0
        assert all(p.platform == "speedyapply" for p in postings)
        # At least one row must route to a vendor and come back with a real JD;
        # a total JD shutout means every host went unrouted (routing regression).
        assert any(p.jd_text and len(p.jd_text) > _MIN_JD_LENGTH for p in postings)


class TestJobSpyLiveSmoke:
    """Small real JobSpy scrape; tolerant of anti-bot blocking (re-run later)."""

    async def test_indeed_jobspy_live(self) -> None:
        """Scrape a small real Indeed query via JobSpy."""
        config = SourcesIndeedConfig(
            enabled=True, search_urls=[_INDEED_QUERY], max_jobs=_JOBSPY_MAX
        )
        source = IndeedSource(config=config, logger=get_logger())
        postings = await source.fetch_jobs({})

        # JobSpy contains anti-bot failures internally → may legitimately return
        # []. Rows with a JD are jobspy_inline; rows JobSpy returned without a
        # description stay unenriched (enrich_source=None) — both are valid.
        assert all(p.platform == "indeed" for p in postings)
        assert all(p.enrich_source in (None, "jobspy_inline") for p in postings)
