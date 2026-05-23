"""Live smoke tests that make real HTTP requests to public ATS job boards.

These tests are never run in CI and are excluded from the default test suite.
Run manually with: pytest -m live -o "addopts="

They exercise the real ATS vendor adapters against known public company boards
to verify that the API shapes have not changed and that the adapters still
parse real-world responses correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.adapters.sources import _ats_ashby as ashby
from jobfeed.adapters.sources import _ats_greenhouse as greenhouse
from jobfeed.adapters.sources import _ats_lever as lever
from jobfeed.adapters.sources._ats_probe import probe_company
from jobfeed.adapters.sources._http import create_http_client

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_JD_LENGTH = 100


# ===========================================================================
# Live smoke tests
# ===========================================================================


class TestATSLiveSmoke:
    """Smoke tests that hit real public ATS boards."""

    async def test_greenhouse_live(self) -> None:
        """Fetch jobs from Anthropic's public Greenhouse board."""
        async with create_http_client() as client:
            jobs = await greenhouse.fetch_jobs(
                client, "anthropic", discovered_at=datetime.now(UTC)
            )
        assert len(jobs) > 0
        assert all(j.platform == "greenhouse" for j in jobs)
        assert all(j.jd_text and len(j.jd_text) > _MIN_JD_LENGTH for j in jobs)

    async def test_ashby_live(self) -> None:
        """Fetch jobs from Ramp's public Ashby board."""
        async with create_http_client() as client:
            jobs = await ashby.fetch_jobs(
                client, "ramp", discovered_at=datetime.now(UTC)
            )
        assert len(jobs) > 0
        assert all(j.platform == "ashby" for j in jobs)
        assert all(j.jd_text and len(j.jd_text) > _MIN_JD_LENGTH for j in jobs)

    async def test_lever_live(self) -> None:
        """Fetch jobs from Netlify's public Lever board."""
        async with create_http_client() as client:
            jobs = await lever.fetch_jobs(
                client, "netlify", discovered_at=datetime.now(UTC)
            )
        assert len(jobs) > 0
        assert all(j.platform == "lever" for j in jobs)
        assert all(j.jd_text and len(j.jd_text) > _MIN_JD_LENGTH for j in jobs)

    async def test_probe_live(self) -> None:
        """Probe Anthropic and verify Greenhouse vendor detection."""
        async with create_http_client() as client:
            vendor = await probe_company(client, "anthropic")
        assert vendor == "greenhouse"
