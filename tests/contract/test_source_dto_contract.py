"""Frozen-fixture DTO contract tests for the Phase 4a sources.

Each new source parses a committed fixture to EXACT ``JobPosting`` field values
under a FIXED reference time, so any drift in a source's field mapping fails the
test deliberately. Coverage: SpeedyApply (markdown + greenhouse routing),
Indeed JobSpy, and LinkedIn JobSpy (DataFrame built from JSON fixtures with
the JobSpy process runner monkeypatched).

This file must NOT require PostgreSQL — it is pure parse + mock and runs inside
``make quality``. All network is mocked (respx for HTTP, monkeypatched JobSpy
process runner); nothing reaches the wire.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import respx

from jobfeed.adapters.sources import _jobspy, _jobspy_process
from jobfeed.adapters.sources import speedyapply as speedyapply_mod
from jobfeed.adapters.sources._ats_greenhouse import JOB_URL as GH_JOB_URL
from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources._speedyapply_markdown import canonical_id_for
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.linkedin_jobspy import LinkedInJobSpySource
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.config import (
    SourcesIndeedConfig,
    SourcesLinkedInJobSpyConfig,
    SourcesSpeedyApplyConfig,
)
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.observability import get_logger

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# FIXED reference time. SpeedyApply derives posted_at as ``now - Nd``; pinning
# the clock makes that field deterministic for the contract.
_FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)

_SPEEDYAPPLY_FIXTURE = "speedyapply_readme.md"
_INDEED_FIXTURE = "jobspy_indeed_rows.json"
_LINKEDIN_FIXTURE = "jobspy_linkedin_rows.json"

# Expected postable-row counts per fixture (🔒 + continuation rows excluded).
_SPEEDYAPPLY_ROW_COUNT = 3
_JOBSPY_ROW_COUNT = 2

# SpeedyApply: README URL + the one routable greenhouse JD endpoint.
_README_URL = "https://lists.example.test/speedyapply.md"
_GH_SLUG = "acmerobotics"
_GH_JOB_ID = "4567890"
_GH_JD_URL = GH_JOB_URL.format(slug=_GH_SLUG, job_id=_GH_JOB_ID)

# Greenhouse single-job response for the routed SpeedyApply row. The content is
# long enough to clear the FULL quality band.
_GH_JD_BODY = (
    "<p>Acme Robotics is hiring a software engineer intern for summer 2026. "
    "You will build motion-planning tooling in Python and C++, write tests, and "
    "ship to our robotics fleet behind feature flags. Strong fundamentals in "
    "algorithms and a love of clean, well-tested code are required. "
    "On this team you will collaborate with senior engineers across perception, "
    "controls, and infrastructure, owning a project end to end from design "
    "review through production rollout and on-call support. You will learn how "
    "large robotics systems are built, tested, and operated in the real world, "
    "with mentorship at every step. We value curiosity, rigor, and a bias toward "
    "shipping. Responsibilities include writing well-tested services, profiling "
    "and optimizing hot paths, instrumenting telemetry, and documenting your "
    "work so the next engineer can build on it. Requirements: currently pursuing "
    "a degree in computer science or a related field, comfortable with data "
    "structures and algorithms, and experienced with at least one systems "
    "language. Nice to have: exposure to ROS, simulation, or real-time control "
    "loops. This body is intentionally long enough to land in the FULL quality "
    "band so the routed SpeedyApply row carries a real JD for the contract.</p>"
)
_GH_SINGLE_JOB: dict[str, Any] = {
    "id": int(_GH_JOB_ID),
    "title": "Robotics Software Engineer Intern",
    "absolute_url": f"https://boards.greenhouse.io/{_GH_SLUG}/jobs/{_GH_JOB_ID}",
    "location": {"name": "San Francisco, CA"},
    "content": _GH_JD_BODY,
    "updated_at": "2026-05-20T09:00:00Z",
    "company_name": "Acme Robotics",
}

# Apply URLs from the SpeedyApply fixture (canonical_id is sha256 of these).
_ACME_APPLY_URL = f"https://boards.greenhouse.io/{_GH_SLUG}/jobs/{_GH_JOB_ID}"
_GLOBEX_APPLY_URL = "https://careers.globex.example.com/postings/swe-intern-001"


def _load_text(name: str) -> str:
    """Read a committed fixture file as text."""
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _load_rows(name: str) -> list[dict[str, Any]]:
    """Read a committed JSON row fixture as a list of dicts."""
    return json.loads(_load_text(name))


def _by_canonical(postings: list[JobPosting], canonical_id: str) -> JobPosting:
    """Return the single posting with the given canonical_id."""
    return next(p for p in postings if p.canonical_id == canonical_id)


# ===========================================================================
# SpeedyApply contract
# ===========================================================================


class TestSpeedyApplyDTOContract:
    """Frozen speedyapply markdown → exact JobPosting field values."""

    @respx.mock
    async def _run(self) -> list[JobPosting]:
        respx.get(_README_URL).respond(200, text=_load_text(_SPEEDYAPPLY_FIXTURE))
        respx.get(_GH_JD_URL).respond(200, json=_GH_SINGLE_JOB)
        config = SourcesSpeedyApplyConfig(enabled=True, search_urls=[_README_URL])
        async with create_http_client() as client:
            source = SpeedyApplySource(
                client=client, config=config, logger=get_logger()
            )
            return await source.fetch_jobs({})

    async def _fetch(self, monkeypatch: pytest.MonkeyPatch) -> list[JobPosting]:
        """Drive the source under a pinned clock so posted_at is deterministic."""

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # noqa: ARG003
                return _FIXED_NOW

        monkeypatch.setattr(speedyapply_mod, "datetime", _FixedDatetime)
        return await self._run()

    async def test_three_postable_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """🔒 row and the empty-company continuation row are skipped."""
        postings = await self._fetch(monkeypatch)
        assert len(postings) == _SPEEDYAPPLY_ROW_COUNT

    async def test_all_postings_platform_speedyapply(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every parsed row is tagged platform='speedyapply'."""
        postings = await self._fetch(monkeypatch)
        assert all(p.platform == "speedyapply" for p in postings)

    async def test_routed_greenhouse_row_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The greenhouse-routed Acme row maps to exact JobPosting fields."""
        postings = await self._fetch(monkeypatch)
        canonical = canonical_id_for(_ACME_APPLY_URL)
        acme = _by_canonical(postings, canonical)
        assert acme.platform == "speedyapply"
        assert acme.canonical_id == canonical
        assert acme.title == "Software Engineer Intern"
        assert acme.company == "Acme Robotics"
        assert acme.location == "San Francisco, CA"
        assert acme.url == _ACME_APPLY_URL
        # JD body comes from the greenhouse single-job fetch → FULL band.
        assert acme.jd_quality == QualityBand.FULL
        assert acme.enrich_source == "speedyapply-greenhouse"
        # Age "2d" against the pinned now.
        assert acme.posted_at == datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)

    async def test_unrouted_globex_row_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The unrouted Globex row keeps an empty JD and the unrouted label."""
        postings = await self._fetch(monkeypatch)
        canonical = canonical_id_for(_GLOBEX_APPLY_URL)
        globex = _by_canonical(postings, canonical)
        assert globex.platform == "speedyapply"
        assert globex.canonical_id == canonical
        assert globex.title == "Backend Engineer Intern"
        assert globex.company == "Globex Systems"
        assert globex.location == "Remote"
        assert globex.url == _GLOBEX_APPLY_URL
        # Unrouted host → no JD fetched → MISSING quality, no enriched_at.
        assert globex.jd_quality == QualityBand.MISSING
        assert globex.enrich_source == "speedyapply-unrouted"
        assert globex.jd_text is None
        assert globex.enriched_at is None
        # Age "5d" against the pinned now.
        assert globex.posted_at == datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)


# ===========================================================================
# JobSpy contract (Indeed + LinkedIn)
# ===========================================================================


def _install_fake_scrape(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    """Replace the JobSpy process runner with one converting ``frame``."""

    def _fake(
        request: _jobspy_process._ScrapeRequest, _timeout_s: float
    ) -> _jobspy_process._ScrapeProcessOutcome:
        return _jobspy_process._ScrapeProcessOutcome(
            postings=_jobspy._frame_to_postings(
                frame,
                platform=request.platform,
                discovered_at=request.discovered_at,
            )
        )

    monkeypatch.setattr(_jobspy_process, "_run_scrape_process", _fake)


def _indeed_frame() -> pd.DataFrame:
    """Build a JobSpy Indeed DataFrame from the fixture.

    Mirrors what JobSpy emits AFTER the dateOnIndeed patch: ``date_posted`` is
    sourced from ``dateOnIndeed`` (not ``datePublished``), so the contract pins
    the post-patch field mapping.
    """
    rows = _load_rows(_INDEED_FIXTURE)
    frame_rows = [
        {
            "id": r["id"],
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "job_url": r["job_url"],
            "description": r["description"],
            "date_posted": r["dateOnIndeed"],
        }
        for r in rows
    ]
    return pd.DataFrame(frame_rows)


def _linkedin_frame() -> pd.DataFrame:
    """Build a JobSpy LinkedIn DataFrame from the fixture."""
    rows = _load_rows(_LINKEDIN_FIXTURE)
    return pd.DataFrame(rows)


class TestIndeedJobSpyDTOContract:
    """Frozen Indeed rows → exact JobPosting field values (platform='indeed')."""

    async def _fetch(self, monkeypatch: pytest.MonkeyPatch) -> list[JobPosting]:
        _install_fake_scrape(monkeypatch, _indeed_frame())
        config = SourcesIndeedConfig(enabled=True, search_urls=["https://indeed/q"])
        source = IndeedSource(config=config, logger=get_logger())
        return await source.fetch_jobs({})

    async def test_two_postings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both fixture rows convert to postings."""
        postings = await self._fetch(monkeypatch)
        assert len(postings) == _JOBSPY_ROW_COUNT

    async def test_first_row_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Wayne row maps to exact JobPosting fields; posted_at=dateOnIndeed."""
        postings = await self._fetch(monkeypatch)
        wayne = _by_canonical(postings, "in-aaa111")
        assert wayne.platform == "indeed"
        assert wayne.canonical_id == "in-aaa111"
        assert wayne.title == "Software Engineer Intern"
        assert wayne.company == "Wayne Enterprises"
        assert wayne.location == "Gotham, NJ"
        assert wayne.url == "https://www.indeed.com/viewjob?jk=aaa111"
        assert wayne.jd_quality == QualityBand.FULL
        assert wayne.enrich_source == "jobspy_inline"
        # dateOnIndeed (2026-05-28), NOT datePublished (2025-11-01).
        assert wayne.posted_at == datetime(2026, 5, 28, tzinfo=UTC)

    async def test_second_row_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Stark row maps to exact JobPosting fields."""
        postings = await self._fetch(monkeypatch)
        stark = _by_canonical(postings, "in-bbb222")
        assert stark.platform == "indeed"
        assert stark.title == "Backend Engineer Intern"
        assert stark.company == "Stark Industries"
        assert stark.location == "Remote"
        assert stark.url == "https://www.indeed.com/viewjob?jk=bbb222"
        assert stark.jd_quality == QualityBand.GOOD
        assert stark.enrich_source == "jobspy_inline"
        assert stark.posted_at == datetime(2026, 5, 27, tzinfo=UTC)


class TestLinkedInJobSpyDTOContract:
    """Frozen LinkedIn rows → exact fields (platform='linkedin_jobspy')."""

    async def _fetch(self, monkeypatch: pytest.MonkeyPatch) -> list[JobPosting]:
        _install_fake_scrape(monkeypatch, _linkedin_frame())
        config = SourcesLinkedInJobSpyConfig(
            enabled=True, search_urls=["https://linkedin/q"]
        )
        source = LinkedInJobSpySource(config=config, logger=get_logger())
        return await source.fetch_jobs({})

    async def test_two_postings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both fixture rows convert to postings."""
        postings = await self._fetch(monkeypatch)
        assert len(postings) == _JOBSPY_ROW_COUNT

    async def test_first_row_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Stripe row maps to exact fields with platform='linkedin_jobspy'."""
        postings = await self._fetch(monkeypatch)
        stripe = _by_canonical(postings, "li-zzz999")
        assert stripe.platform == "linkedin_jobspy"
        assert stripe.canonical_id == "li-zzz999"
        assert stripe.title == "Backend Engineer"
        assert stripe.company == "Stripe"
        assert stripe.location == "Remote - US"
        assert stripe.url == "https://www.linkedin.com/jobs/view/zzz999"
        assert stripe.jd_quality == QualityBand.FULL
        assert stripe.enrich_source == "jobspy_inline"
        assert stripe.posted_at == datetime(2026, 5, 26, tzinfo=UTC)

    async def test_second_row_exact_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Datadog row maps to exact fields."""
        postings = await self._fetch(monkeypatch)
        datadog = _by_canonical(postings, "li-yyy888")
        assert datadog.platform == "linkedin_jobspy"
        assert datadog.title == "Site Reliability Engineer"
        assert datadog.company == "Datadog"
        assert datadog.location == "New York, NY"
        assert datadog.url == "https://www.linkedin.com/jobs/view/yyy888"
        assert datadog.jd_quality == QualityBand.GOOD
        assert datadog.enrich_source == "jobspy_inline"
        assert datadog.posted_at == datetime(2026, 5, 25, tzinfo=UTC)
