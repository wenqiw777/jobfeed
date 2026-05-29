"""Frozen-fixture DTO contract tests for Greenhouse, Ashby, and Lever adapters.

Each test loads a committed JSON fixture, mocks the HTTP call with respx, invokes
the vendor's fetch_jobs, and asserts exact field values on the returned JobPosting
list. These tests serve as regression guards: if a vendor's API response shape or
the adapter's mapping logic changes, the contract test fails deliberately.

Fixture files live in tests/fixtures/ and are committed as golden files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from jobfeed.adapters.sources._ats_ashby import JOBS_URL as ASHBY_JOBS_URL
from jobfeed.adapters.sources._ats_ashby import fetch_jobs as ashby_fetch_jobs
from jobfeed.adapters.sources._ats_greenhouse import JOBS_URL as GH_JOBS_URL
from jobfeed.adapters.sources._ats_greenhouse import fetch_jobs as gh_fetch_jobs
from jobfeed.adapters.sources._ats_lever import JOBS_URL as LEVER_JOBS_URL
from jobfeed.adapters.sources._ats_lever import fetch_jobs as lever_fetch_jobs
from jobfeed.adapters.sources._http import create_http_client
from jobfeed.domain.models import QualityBand

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SLUG = "testcorp"
DISCOVERED_AT = datetime(2026, 5, 23, 0, 0, 0, tzinfo=UTC)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

_GH_URL = GH_JOBS_URL.format(slug=SLUG)
_ASHBY_URL = ASHBY_JOBS_URL.format(slug=SLUG)
_LEVER_URL = LEVER_JOBS_URL.format(slug=SLUG)

# Fixture has exactly this many jobs per vendor.
_EXPECTED_POSTING_COUNT = 4

_GH_FIXTURE = "ats_greenhouse_response.json"
_ASHBY_FIXTURE = "ats_ashby_response.json"
_LEVER_FIXTURE = "ats_lever_response.json"


def _load_fixture(name: str) -> bytes:
    """Load a frozen fixture file as raw bytes for respx."""
    return (_FIXTURES_DIR / name).read_bytes()


def _gh_response() -> httpx.Response:
    return httpx.Response(200, content=_load_fixture(_GH_FIXTURE))


def _ashby_response() -> httpx.Response:
    return httpx.Response(200, content=_load_fixture(_ASHBY_FIXTURE))


def _lever_response() -> httpx.Response:
    return httpx.Response(200, content=_load_fixture(_LEVER_FIXTURE))


# ===========================================================================
# GREENHOUSE contract tests
# ===========================================================================


class TestGreenhouseDTOContract:
    """Contract tests: frozen Greenhouse fixture → exact JobPosting field values."""

    @respx.mock
    async def test_fixture_yields_four_postings(self) -> None:
        """All four jobs in the Greenhouse fixture are parsed successfully."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == _EXPECTED_POSTING_COUNT

    @respx.mock
    async def test_platform_is_greenhouse_on_all_postings(self) -> None:
        """Every posting from the Greenhouse fixture has platform='greenhouse'."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.platform == "greenhouse" for p in postings)

    @respx.mock
    async def test_enrich_source_on_all_postings(self) -> None:
        """Every Greenhouse posting has enrich_source='api-greenhouse'."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.enrich_source == "api-greenhouse" for p in postings)

    @respx.mock
    async def test_discovered_at_stamped_on_all_postings(self) -> None:
        """Every posting from the Greenhouse fixture has the caller's discovered_at."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.discovered_at == DISCOVERED_AT for p in postings)

    @respx.mock
    async def test_enriched_at_equals_discovered_at_on_all_postings(self) -> None:
        """Every Greenhouse posting has enriched_at equal to discovered_at."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.enriched_at == DISCOVERED_AT for p in postings)

    @respx.mock
    async def test_jd_quality_is_set_on_all_postings(self) -> None:
        """Every Greenhouse posting has a non-None jd_quality."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.jd_quality is not None for p in postings)

    @respx.mock
    async def test_job_10001_exact_field_mapping(self) -> None:
        """Job 10001 maps to exact canonical_id, title, company, location, quality."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        posting = next(p for p in postings if p.canonical_id == "10001")
        assert posting.title == "Senior Backend Engineer"
        assert posting.url == "https://boards.greenhouse.io/testcorp/jobs/10001"
        assert posting.company == "Testcorp Inc"
        assert posting.location == "San Francisco, CA"
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.discovered_at == DISCOVERED_AT
        assert posting.enriched_at == DISCOVERED_AT
        assert posting.enrich_source == "api-greenhouse"
        assert posting.posted_at == datetime(2026, 5, 10, 14, 30, tzinfo=UTC)

    @respx.mock
    async def test_job_10002_null_location_name_yields_empty_location(self) -> None:
        """Job 10002 has location.name=null which maps to location=''."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        posting = next(p for p in postings if p.canonical_id == "10002")
        # null location.name → empty string
        assert posting.location == ""
        # no company_name → falls back to slug
        assert posting.company == SLUG
        assert posting.jd_quality == QualityBand.PARTIAL

    @respx.mock
    async def test_job_10003_html_entities_unescaped_in_jd_text(self) -> None:
        """Job 10003 content has &amp; and &apos; entities that must be unescaped."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        posting = next(p for p in postings if p.canonical_id == "10003")
        assert posting.jd_text is not None
        # HTML entities must be unescaped: &amp; → &, &apos; → '
        assert "&amp;" not in posting.jd_text
        assert "&apos;" not in posting.jd_text
        assert "Testcorp's" in posting.jd_text
        assert "Spark & dbt" in posting.jd_text
        assert posting.company == "Testcorp Data"
        assert posting.location == "New York, NY"
        assert posting.jd_quality == QualityBand.GOOD

    @respx.mock
    async def test_job_10004_exact_field_mapping(self) -> None:
        """Job 10004 maps to exact canonical_id, location, company (slug fallback)."""
        respx.get(_GH_URL).mock(return_value=_gh_response())
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        posting = next(p for p in postings if p.canonical_id == "10004")
        assert posting.title == "Frontend Engineer"
        assert posting.location == "Remote"
        assert posting.company == SLUG
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.posted_at == datetime(2026, 5, 14, 16, 45, tzinfo=UTC)


# ===========================================================================
# ASHBY contract tests
# ===========================================================================


class TestAshbyDTOContract:
    """Contract tests: frozen Ashby fixture → exact JobPosting field values."""

    @respx.mock
    async def test_fixture_yields_four_postings(self) -> None:
        """All four jobs in the Ashby fixture are parsed successfully."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == _EXPECTED_POSTING_COUNT

    @respx.mock
    async def test_platform_is_ashby_on_all_postings(self) -> None:
        """Every posting from the Ashby fixture has platform='ashby'."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.platform == "ashby" for p in postings)

    @respx.mock
    async def test_enrich_source_on_all_postings(self) -> None:
        """Every posting from the Ashby fixture has enrich_source='api-ashby'."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.enrich_source == "api-ashby" for p in postings)

    @respx.mock
    async def test_discovered_at_stamped_on_all_postings(self) -> None:
        """Every posting from the Ashby fixture has the caller's discovered_at."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.discovered_at == DISCOVERED_AT for p in postings)

    @respx.mock
    async def test_enriched_at_equals_discovered_at_on_all_postings(self) -> None:
        """Every Ashby posting has enriched_at equal to discovered_at."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.enriched_at == DISCOVERED_AT for p in postings)

    @respx.mock
    async def test_company_is_slug_on_all_postings(self) -> None:
        """Every Ashby posting uses the slug as company name."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.company == SLUG for p in postings)

    @respx.mock
    async def test_job_a1b2_senior_swe_exact_field_mapping(self) -> None:
        """Job a1b2c3d4 maps to exact title, location, quality, posted_at."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Senior Software Engineer"
        assert posting.url == f"https://jobs.ashbyhq.com/testcorp/{canonical}"
        assert posting.location == "San Francisco, CA"
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.posted_at == datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
        # descriptionPlain was present; jd_text starts with the plain text
        assert posting.jd_text is not None
        assert posting.jd_text.startswith("We are seeking a Senior Software Engineer")

    @respx.mock
    async def test_job_b2c3_null_description_plain_falls_back_to_html(self) -> None:
        """Job b2c3d4e5: descriptionPlain=null falls back to descriptionHtml."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Machine Learning Engineer"
        # null location → empty string
        assert posting.location == ""
        # jd_text extracted from descriptionHtml (no raw HTML tags)
        assert posting.jd_text is not None
        assert "<p>" not in posting.jd_text
        assert "Join our ML team" in posting.jd_text
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.discovered_at == DISCOVERED_AT
        assert posting.enriched_at == DISCOVERED_AT
        assert posting.enrich_source == "api-ashby"

    @respx.mock
    async def test_job_c3d4_product_designer_exact_field_mapping(self) -> None:
        """Job c3d4e5f6 maps to exact location, quality, and posted_at."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "c3d4e5f6-a7b8-9012-cdef-123456789012"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Product Designer"
        assert posting.location == "New York, NY"
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.posted_at == datetime(2026, 5, 5, 12, 0, tzinfo=UTC)

    @respx.mock
    async def test_job_d4e5_sre_exact_field_mapping(self) -> None:
        """Job d4e5f6a7 maps to exact location, quality, and posted_at."""
        respx.get(_ASHBY_URL).mock(return_value=_ashby_response())
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "d4e5f6a7-b8c9-0123-defa-234567890123"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Site Reliability Engineer"
        assert posting.location == "Remote - US"
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.posted_at == datetime(2026, 5, 7, 14, 0, tzinfo=UTC)


# ===========================================================================
# LEVER contract tests
# ===========================================================================


class TestLeverDTOContract:
    """Contract tests: frozen Lever fixture → exact JobPosting field values."""

    @respx.mock
    async def test_fixture_yields_four_postings(self) -> None:
        """All four jobs in the Lever fixture are parsed successfully."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == _EXPECTED_POSTING_COUNT

    @respx.mock
    async def test_platform_is_lever_on_all_postings(self) -> None:
        """Every posting from the Lever fixture has platform='lever'."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.platform == "lever" for p in postings)

    @respx.mock
    async def test_enrich_source_on_all_postings(self) -> None:
        """Every posting from the Lever fixture has enrich_source='api-lever'."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.enrich_source == "api-lever" for p in postings)

    @respx.mock
    async def test_discovered_at_stamped_on_all_postings(self) -> None:
        """Every posting from the Lever fixture has the caller's discovered_at."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.discovered_at == DISCOVERED_AT for p in postings)

    @respx.mock
    async def test_enriched_at_equals_discovered_at_on_all_postings(self) -> None:
        """Every Lever posting has enriched_at equal to discovered_at."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.enriched_at == DISCOVERED_AT for p in postings)

    @respx.mock
    async def test_company_is_slug_on_all_postings(self) -> None:
        """Every Lever posting uses the slug as company name."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert all(p.company == SLUG for p in postings)

    @respx.mock
    async def test_job_e5f6_swe_epoch_ms_posted_at(self) -> None:
        """Job e5f6 has createdAt=1716400800000 (epoch ms) → correct UTC posted_at."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "e5f6a7b8-c9d0-1234-efab-345678901234"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Software Engineer, Backend"
        assert posting.url == f"https://jobs.lever.co/testcorp/{canonical}"
        assert posting.location == "San Francisco, CA"
        # 1716400800000 ms = 2024-05-22T18:00:00Z
        assert posting.posted_at == datetime(2024, 5, 22, 18, 0, tzinfo=UTC)
        assert posting.posted_at.tzinfo == UTC
        assert posting.jd_quality == QualityBand.GOOD
        # jd_text combines descriptionPlain + lists[].text headings + lists[].content
        assert posting.jd_text is not None
        assert "Build the backend services" in posting.jd_text
        assert "Requirements:" in posting.jd_text
        assert "Nice to Have:" in posting.jd_text
        assert "PostgreSQL" in posting.jd_text

    @respx.mock
    async def test_job_f6a7_null_categories_location_yields_empty_location(
        self,
    ) -> None:
        """Job f6a7 has categories.location=null which maps to location=''."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "f6a7b8c9-d0e1-2345-fabc-456789012345"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Data Scientist"
        # categories.location is null → empty string
        assert posting.location == ""
        assert posting.jd_quality == QualityBand.PARTIAL
        assert posting.discovered_at == DISCOVERED_AT
        assert posting.enriched_at == DISCOVERED_AT
        assert posting.enrich_source == "api-lever"

    @respx.mock
    async def test_job_a7b8_engineering_manager_exact_field_mapping(self) -> None:
        """Job a7b8 maps to exact title, location, quality, and posted_at."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "a7b8c9d0-e1f2-3456-abcd-567890123456"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Engineering Manager"
        assert posting.location == "New York, NY"
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.posted_at == datetime(2024, 5, 24, 18, 0, tzinfo=UTC)

    @respx.mock
    async def test_job_b8c9_technical_recruiter_exact_field_mapping(self) -> None:
        """Job b8c9 maps to exact title, location, and posted_at."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        canonical = "b8c9d0e1-f2a3-4567-bcde-678901234567"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.title == "Technical Recruiter"
        assert posting.location == "Remote"
        assert posting.jd_quality == QualityBand.GOOD
        assert posting.posted_at == datetime(2024, 5, 25, 18, 0, tzinfo=UTC)

    @respx.mock
    async def test_jd_text_combines_description_plain_list_headings_and_content(
        self,
    ) -> None:
        """Lever jd_text includes descriptionPlain, headings, and content."""
        respx.get(_LEVER_URL).mock(return_value=_lever_response())
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        # Job e5f6 has both descriptionPlain and two lists
        canonical = "e5f6a7b8-c9d0-1234-efab-345678901234"
        posting = next(p for p in postings if p.canonical_id == canonical)
        assert posting.jd_text is not None
        # plain text intro present
        assert "Build the backend services" in posting.jd_text
        # list content was HTML-stripped and appended
        assert "<ul>" not in posting.jd_text
        assert "<li>" not in posting.jd_text
        # headings and items from lists are present as plain text
        assert "Requirements:" in posting.jd_text
        assert "Nice to Have:" in posting.jd_text
        assert "Python or Go" in posting.jd_text
        assert "Kafka" in posting.jd_text
