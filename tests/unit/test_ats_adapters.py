"""Unit tests for Greenhouse, Ashby, and Lever vendor ATS adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from jobfeed.adapters.sources._ats_ashby import fetch_jobs as ashby_fetch_jobs
from jobfeed.adapters.sources._ats_ashby import probe as ashby_probe
from jobfeed.adapters.sources._ats_greenhouse import fetch_jobs as gh_fetch_jobs
from jobfeed.adapters.sources._ats_greenhouse import probe as gh_probe
from jobfeed.adapters.sources._ats_lever import fetch_jobs as lever_fetch_jobs
from jobfeed.adapters.sources._ats_lever import probe as lever_probe
from jobfeed.adapters.sources._http import (
    ATSFetchError,
    ATSParseError,
    ProbeIndeterminateError,
    ProbeNetworkError,
    create_http_client,
)
from jobfeed.domain.models import JobPosting, QualityBand

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SLUG = "acme"
DISCOVERED_AT = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)

GH_JOBS_URL = f"https://boards-api.greenhouse.io/v1/boards/{SLUG}/jobs?content=true"
GH_PROBE_URL = f"https://boards-api.greenhouse.io/v1/boards/{SLUG}"

ASHBY_JOBS_URL = (
    f"https://api.ashbyhq.com/posting-api/job-board/{SLUG}?includeCompensation=true"
)

LEVER_JOBS_URL = f"https://api.lever.co/v0/postings/{SLUG}?mode=json"

HTTP_200 = 200
HTTP_404 = 404
HTTP_410 = 410
HTTP_403 = 403
HTTP_429 = 429
HTTP_500 = 500


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_GH_DEFAULT_CONTENT = "<p>Build distributed systems at scale. " + "A" * 200 + "</p>"
_ASHBY_DEFAULT_PLAIN = "Build great things at our company. " + "B" * 200
_LEVER_DEFAULT_PLAIN = "Join our ML team. " + "C" * 200
GH_MULTI_JOB_COUNT = 3


def _gh_job(**overrides: object) -> dict:
    """Build a synthetic Greenhouse job dict with optional field overrides."""
    base: dict = {
        "id": overrides.pop("job_id", 1001),
        "title": overrides.pop("title", "Backend Engineer"),
        "absolute_url": overrides.pop(
            "url", "https://boards.greenhouse.io/acme/jobs/1001"
        ),
        "location": {"name": overrides.pop("location_name", "San Francisco, CA")},
        "content": overrides.pop("content", _GH_DEFAULT_CONTENT),
        "updated_at": overrides.pop("updated_at", "2026-05-01T10:00:00Z"),
    }
    company_name = overrides.pop("company_name", None)
    if company_name is not None:
        base["company_name"] = company_name
    base.update(overrides)
    return base


def _ashby_job(**overrides: object) -> dict:
    """Build a synthetic Ashby job dict with optional field overrides."""
    base: dict = {
        "id": overrides.pop("job_id", "ashby-123"),
        "title": overrides.pop("title", "Senior SWE"),
        "jobUrl": overrides.pop("job_url", "https://jobs.ashbyhq.com/acme/123"),
        "location": overrides.pop("location", "Remote"),
        "descriptionPlain": overrides.pop("description_plain", _ASHBY_DEFAULT_PLAIN),
        "descriptionHtml": overrides.pop("description_html", None),
        "publishedAt": overrides.pop("published_at", "2026-04-15T08:00:00Z"),
    }
    base.update(overrides)
    return base


def _lever_job(**overrides: object) -> dict:
    """Build a synthetic Lever job dict with optional field overrides."""
    base: dict = {
        "id": overrides.pop("job_id", "lever-abc-123"),
        "text": overrides.pop("text", "ML Engineer"),
        "hostedUrl": overrides.pop(
            "hosted_url", "https://jobs.lever.co/acme/lever-abc-123"
        ),
        "categories": {"location": overrides.pop("location", "New York, NY")},
        "descriptionPlain": overrides.pop("description_plain", _LEVER_DEFAULT_PLAIN),
        "lists": overrides.pop("lists", []),
        "createdAt": overrides.pop("created_at", 1_748_000_000_000),
    }
    base.update(overrides)
    return base


# ===========================================================================
# GREENHOUSE
# ===========================================================================


class TestGreenhouseProbe:
    """Tests for greenhouse probe() function."""

    @respx.mock
    async def test_probe_returns_true_on_200(self) -> None:
        """probe returns True when Greenhouse board endpoint returns 200."""
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))
        async with create_http_client() as client:
            result = await gh_probe(client, SLUG)
        assert result is True

    @respx.mock
    async def test_probe_returns_false_on_404(self) -> None:
        """probe returns False when board endpoint returns 404."""
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_404))
        async with create_http_client() as client:
            result = await gh_probe(client, SLUG)
        assert result is False

    @respx.mock
    async def test_probe_returns_false_on_410(self) -> None:
        """probe returns False when board endpoint returns 410."""
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_410))
        async with create_http_client() as client:
            result = await gh_probe(client, SLUG)
        assert result is False

    @respx.mock
    async def test_probe_raises_probe_network_error_on_timeout(self) -> None:
        """probe raises ProbeNetworkError on timeout."""
        respx.head(GH_PROBE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await gh_probe(client, SLUG)

    @respx.mock
    async def test_probe_raises_probe_indeterminate_on_403(self) -> None:
        """probe raises ProbeIndeterminateError on 403."""
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_403))
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await gh_probe(client, SLUG)

    @respx.mock
    async def test_probe_raises_probe_indeterminate_on_500(self) -> None:
        """probe raises ProbeIndeterminateError on 500."""
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_500))
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await gh_probe(client, SLUG)


class TestGreenhouseFetchJobs:
    """Tests for greenhouse fetch_jobs() function."""

    @respx.mock
    async def test_fetch_jobs_returns_postings_on_200(self) -> None:
        """fetch_jobs returns JobPosting list for valid 200 response."""
        job = _gh_job()
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert isinstance(postings[0], JobPosting)

    @respx.mock
    async def test_fetch_jobs_sets_platform_greenhouse(self) -> None:
        """fetch_jobs stamps platform='greenhouse' on each posting."""
        job = _gh_job()
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].platform == "greenhouse"

    @respx.mock
    async def test_fetch_jobs_sets_enrich_source(self) -> None:
        """fetch_jobs stamps enrich_source='api-greenhouse'."""
        job = _gh_job()
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].enrich_source == "api-greenhouse"

    @respx.mock
    async def test_fetch_jobs_sets_discovered_at(self) -> None:
        """fetch_jobs stamps caller-provided discovered_at."""
        job = _gh_job()
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].discovered_at == DISCOVERED_AT

    @respx.mock
    async def test_fetch_jobs_parses_canonical_id(self) -> None:
        """fetch_jobs converts job id to string canonical_id."""
        job = _gh_job(job_id=9999)
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].canonical_id == "9999"

    @respx.mock
    async def test_fetch_jobs_uses_slug_as_company_fallback(self) -> None:
        """fetch_jobs uses slug as company when company_name is absent."""
        job = _gh_job()
        job.pop("company_name", None)
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].company == SLUG

    @respx.mock
    async def test_fetch_jobs_uses_company_name_when_present(self) -> None:
        """fetch_jobs uses company_name field when present."""
        job = _gh_job(company_name="Acme Corp")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].company == "Acme Corp"

    @respx.mock
    async def test_fetch_jobs_extracts_location_from_nested_object(self) -> None:
        """fetch_jobs extracts location name from nested location object."""
        job = _gh_job(location_name="Austin, TX")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == "Austin, TX"

    @respx.mock
    async def test_fetch_jobs_null_location_defaults_to_empty_string(self) -> None:
        """fetch_jobs defaults to empty string when location is null."""
        job = _gh_job()
        job["location"] = None
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == ""

    @respx.mock
    async def test_fetch_jobs_missing_location_defaults_to_empty_string(self) -> None:
        """fetch_jobs defaults to empty string when location key is absent."""
        job = _gh_job()
        job.pop("location", None)
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == ""

    @respx.mock
    async def test_fetch_jobs_converts_html_content_to_text(self) -> None:
        """fetch_jobs strips HTML tags from job content field."""
        job = _gh_job(content="<p>Build <b>systems</b>. " + "X" * 200 + "</p>")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert "<p>" not in postings[0].jd_text
        assert "systems" in postings[0].jd_text

    @respx.mock
    async def test_fetch_jobs_parses_updated_at_as_posted_at(self) -> None:
        """fetch_jobs parses updated_at into posted_at datetime."""
        job = _gh_job(updated_at="2026-05-01T10:00:00Z")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is not None

    @respx.mock
    async def test_fetch_jobs_invalid_updated_at_becomes_none(self) -> None:
        """fetch_jobs sets posted_at=None when updated_at is invalid."""
        job = _gh_job(updated_at="not-a-date")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is None

    @respx.mock
    async def test_fetch_jobs_null_updated_at_becomes_none(self) -> None:
        """fetch_jobs sets posted_at=None when updated_at is null."""
        job = _gh_job(updated_at=None)
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is None

    @respx.mock
    async def test_fetch_jobs_raises_ats_parse_error_on_non_dict_response(
        self,
    ) -> None:
        """fetch_jobs raises ATSParseError when top-level response is not a dict."""
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[{"id": 1}])
        )
        async with create_http_client() as client:
            with pytest.raises(ATSParseError):
                await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_raises_ats_parse_error_on_missing_jobs_key(
        self,
    ) -> None:
        """fetch_jobs raises ATSParseError when 'jobs' key is missing."""
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"meta": {}})
        )
        async with create_http_client() as client:
            with pytest.raises(ATSParseError):
                await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_raises_ats_parse_error_when_jobs_not_list(
        self,
    ) -> None:
        """fetch_jobs raises ATSParseError when 'jobs' value is not a list."""
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": "bad"})
        )
        async with create_http_client() as client:
            with pytest.raises(ATSParseError):
                await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_returns_empty_list_for_empty_jobs_array(
        self,
    ) -> None:
        """fetch_jobs returns empty list when jobs array is empty."""
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": []})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []

    @respx.mock
    async def test_fetch_jobs_skips_job_with_null_id(self) -> None:
        """fetch_jobs skips jobs where id is null."""
        valid_job = _gh_job(job_id=1)
        invalid_job = _gh_job(job_id=2)
        invalid_job["id"] = None
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(
                HTTP_200, json={"jobs": [valid_job, invalid_job]}
            )
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert postings[0].canonical_id == "1"

    @respx.mock
    async def test_fetch_jobs_skips_job_with_blank_title(self) -> None:
        """fetch_jobs skips jobs with blank title."""
        job = _gh_job(title="")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []

    @respx.mock
    async def test_fetch_jobs_skips_job_with_blank_url(self) -> None:
        """fetch_jobs skips jobs with blank URL."""
        job = _gh_job(url="")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []

    @respx.mock
    async def test_fetch_jobs_skips_job_with_blank_jd_text(self) -> None:
        """fetch_jobs skips jobs with empty content/jd_text."""
        job = _gh_job(content="")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []

    @respx.mock
    async def test_fetch_jobs_isolates_malformed_job_continues_with_rest(
        self,
    ) -> None:
        """fetch_jobs skips a malformed job dict but returns other valid jobs."""
        good_job = _gh_job(job_id=1)
        bad_job = "not a dict"
        another_good = _gh_job(job_id=2)
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(
                HTTP_200, json={"jobs": [good_job, bad_job, another_good]}
            )
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        ids = {p.canonical_id for p in postings}
        assert "1" in ids
        assert "2" in ids

    @respx.mock
    async def test_fetch_jobs_raises_ats_fetch_error_on_http_error(self) -> None:
        """fetch_jobs raises ATSFetchError on non-2xx HTTP status."""
        respx.get(GH_JOBS_URL).mock(return_value=httpx.Response(HTTP_500))
        async with create_http_client() as client:
            with pytest.raises(ATSFetchError):
                await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_sets_jd_quality(self) -> None:
        """fetch_jobs sets jd_quality via assess_quality."""
        long_content = "Word " * 300
        job = _gh_job(content=f"<p>{long_content}</p>")
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].jd_quality in {
            QualityBand.FULL,
            QualityBand.GOOD,
            QualityBand.PARTIAL,
        }

    @respx.mock
    async def test_fetch_jobs_enriched_at_equals_discovered_at(self) -> None:
        """fetch_jobs sets enriched_at equal to discovered_at."""
        job = _gh_job()
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].enriched_at == DISCOVERED_AT

    @respx.mock
    async def test_fetch_jobs_multiple_jobs_all_returned(self) -> None:
        """fetch_jobs returns all valid jobs from a multi-job response."""
        jobs = [
            _gh_job(job_id=i, url=f"https://example.com/{i}")
            for i in range(1, GH_MULTI_JOB_COUNT + 1)
        ]
        respx.get(GH_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": jobs})
        )
        async with create_http_client() as client:
            postings = await gh_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == GH_MULTI_JOB_COUNT


# ===========================================================================
# ASHBY
# ===========================================================================


class TestAshbyProbe:
    """Tests for Ashby probe() function."""

    @respx.mock
    async def test_probe_returns_true_on_200_with_get(self) -> None:
        """Ashby probe uses GET and returns True on 200."""
        respx.get(ASHBY_JOBS_URL).mock(return_value=httpx.Response(HTTP_200))
        async with create_http_client() as client:
            result = await ashby_probe(client, SLUG)
        assert result is True

    @respx.mock
    async def test_probe_returns_false_on_404(self) -> None:
        """Ashby probe returns False on 404."""
        respx.get(ASHBY_JOBS_URL).mock(return_value=httpx.Response(HTTP_404))
        async with create_http_client() as client:
            result = await ashby_probe(client, SLUG)
        assert result is False

    @respx.mock
    async def test_probe_returns_false_on_410(self) -> None:
        """Ashby probe returns False on 410."""
        respx.get(ASHBY_JOBS_URL).mock(return_value=httpx.Response(HTTP_410))
        async with create_http_client() as client:
            result = await ashby_probe(client, SLUG)
        assert result is False

    @respx.mock
    async def test_probe_raises_probe_network_error_on_timeout(self) -> None:
        """Ashby probe raises ProbeNetworkError on timeout."""
        respx.get(ASHBY_JOBS_URL).mock(side_effect=httpx.TimeoutException("timed out"))
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await ashby_probe(client, SLUG)

    @respx.mock
    async def test_probe_raises_probe_indeterminate_on_429(self) -> None:
        """Ashby probe raises ProbeIndeterminateError on 429."""
        respx.get(ASHBY_JOBS_URL).mock(return_value=httpx.Response(HTTP_429))
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await ashby_probe(client, SLUG)


class TestAshbyFetchJobs:
    """Tests for Ashby fetch_jobs() function."""

    @respx.mock
    async def test_fetch_jobs_returns_postings_on_200(self) -> None:
        """Ashby fetch_jobs returns JobPosting list for valid response."""
        job = _ashby_job()
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert isinstance(postings[0], JobPosting)

    @respx.mock
    async def test_fetch_jobs_sets_platform_ashby(self) -> None:
        """Ashby fetch_jobs stamps platform='ashby'."""
        job = _ashby_job()
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].platform == "ashby"

    @respx.mock
    async def test_fetch_jobs_sets_enrich_source_api_ashby(self) -> None:
        """Ashby fetch_jobs stamps enrich_source='api-ashby'."""
        job = _ashby_job()
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].enrich_source == "api-ashby"

    @respx.mock
    async def test_fetch_jobs_uses_slug_as_company(self) -> None:
        """Ashby fetch_jobs uses slug as company name."""
        job = _ashby_job()
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].company == SLUG

    @respx.mock
    async def test_fetch_jobs_prefers_description_plain_over_html(self) -> None:
        """Ashby fetch_jobs uses descriptionPlain when present."""
        job = _ashby_job(
            description_plain="Plain text description. " + "Z" * 200,
            description_html="<p>HTML description</p>",
        )
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert "Plain text" in postings[0].jd_text
        assert "<p>" not in postings[0].jd_text

    @respx.mock
    async def test_fetch_jobs_falls_back_to_html_when_plain_missing(self) -> None:
        """Ashby fetch_jobs falls back to descriptionHtml when plain is absent."""
        job = _ashby_job(
            description_plain=None,
            description_html="<p>HTML only description. " + "Y" * 200 + "</p>",
        )
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert "HTML only description" in postings[0].jd_text
        assert "<p>" not in postings[0].jd_text

    @respx.mock
    async def test_fetch_jobs_extracts_string_location(self) -> None:
        """Ashby fetch_jobs handles location as a string."""
        job = _ashby_job(location="Boston, MA")
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == "Boston, MA"

    @respx.mock
    async def test_fetch_jobs_null_location_defaults_to_empty_string(self) -> None:
        """Ashby fetch_jobs defaults to empty string for null location."""
        job = _ashby_job(location=None)
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == ""

    @respx.mock
    async def test_fetch_jobs_parses_published_at(self) -> None:
        """Ashby fetch_jobs parses publishedAt into posted_at."""
        job = _ashby_job(published_at="2026-04-15T08:00:00Z")
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is not None

    @respx.mock
    async def test_fetch_jobs_invalid_published_at_becomes_none(self) -> None:
        """Ashby fetch_jobs sets posted_at=None for invalid publishedAt."""
        job = _ashby_job(published_at="not-a-date")
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is None

    @respx.mock
    async def test_fetch_jobs_raises_ats_parse_error_on_missing_jobs_key(
        self,
    ) -> None:
        """Ashby fetch_jobs raises ATSParseError when 'jobs' key is missing."""
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"data": []})
        )
        async with create_http_client() as client:
            with pytest.raises(ATSParseError):
                await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_raises_ats_parse_error_when_jobs_not_list(
        self,
    ) -> None:
        """Ashby fetch_jobs raises ATSParseError when 'jobs' value is not a list."""
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": {"nested": "dict"}})
        )
        async with create_http_client() as client:
            with pytest.raises(ATSParseError):
                await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_skips_job_with_null_id(self) -> None:
        """Ashby fetch_jobs skips jobs where id is null."""
        valid_job = _ashby_job(job_id="valid-1")
        null_id_job = _ashby_job(job_id="null-2")
        null_id_job["id"] = None
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(
                HTTP_200, json={"jobs": [valid_job, null_id_job]}
            )
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert postings[0].canonical_id == "valid-1"

    @respx.mock
    async def test_fetch_jobs_isolates_malformed_job_continues_with_rest(
        self,
    ) -> None:
        """Ashby fetch_jobs skips malformed job but returns other valid jobs."""
        good = _ashby_job(job_id="good-1")
        bad = "not a dict at all"
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [good, bad]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert postings[0].canonical_id == "good-1"

    @respx.mock
    async def test_fetch_jobs_skips_job_with_blank_jd_text(self) -> None:
        """Ashby fetch_jobs skips jobs with no description content."""
        job = _ashby_job(description_plain=None, description_html=None)
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []


# ===========================================================================
# LEVER
# ===========================================================================


class TestLeverProbe:
    """Tests for Lever probe() function."""

    @respx.mock
    async def test_probe_returns_true_on_200_with_valid_json_list(self) -> None:
        """Lever probe returns True on 200 with JSON list."""
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[_lever_job()])
        )
        async with create_http_client() as client:
            result = await lever_probe(client, SLUG)
        assert result is True

    @respx.mock
    async def test_probe_returns_false_on_404(self) -> None:
        """Lever probe returns False on 404."""
        respx.get(LEVER_JOBS_URL).mock(return_value=httpx.Response(HTTP_404))
        async with create_http_client() as client:
            result = await lever_probe(client, SLUG)
        assert result is False

    @respx.mock
    async def test_probe_returns_false_on_410(self) -> None:
        """Lever probe returns False on 410."""
        respx.get(LEVER_JOBS_URL).mock(return_value=httpx.Response(HTTP_410))
        async with create_http_client() as client:
            result = await lever_probe(client, SLUG)
        assert result is False

    @respx.mock
    async def test_probe_raises_probe_network_error_on_timeout(self) -> None:
        """Lever probe raises ProbeNetworkError on timeout."""
        respx.get(LEVER_JOBS_URL).mock(side_effect=httpx.TimeoutException("timed out"))
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await lever_probe(client, SLUG)

    @respx.mock
    async def test_probe_raises_probe_indeterminate_on_500(self) -> None:
        """Lever probe raises ProbeIndeterminateError on 500."""
        respx.get(LEVER_JOBS_URL).mock(return_value=httpx.Response(HTTP_500))
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await lever_probe(client, SLUG)

    @respx.mock
    async def test_probe_raises_indeterminate_on_200_with_non_list_json(
        self,
    ) -> None:
        """Lever probe raises ProbeIndeterminateError when 2xx returns non-list JSON."""
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": []})
        )
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await lever_probe(client, SLUG)

    @respx.mock
    async def test_probe_raises_indeterminate_on_200_with_invalid_json(
        self,
    ) -> None:
        """Lever probe raises ProbeIndeterminateError on 200 with non-JSON body."""
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, content=b"<html>not json</html>")
        )
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await lever_probe(client, SLUG)

    @respx.mock
    async def test_probe_returns_true_on_200_with_empty_list(self) -> None:
        """Lever probe returns True on 200 with empty list (valid board, no jobs)."""
        respx.get(LEVER_JOBS_URL).mock(return_value=httpx.Response(HTTP_200, json=[]))
        async with create_http_client() as client:
            result = await lever_probe(client, SLUG)
        assert result is True


class TestLeverFetchJobs:
    """Tests for Lever fetch_jobs() function."""

    @respx.mock
    async def test_fetch_jobs_returns_postings_from_array_response(self) -> None:
        """Lever fetch_jobs handles top-level array response."""
        job = _lever_job()
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert isinstance(postings[0], JobPosting)

    @respx.mock
    async def test_fetch_jobs_sets_platform_lever(self) -> None:
        """Lever fetch_jobs stamps platform='lever'."""
        job = _lever_job()
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].platform == "lever"

    @respx.mock
    async def test_fetch_jobs_sets_enrich_source_api_lever(self) -> None:
        """Lever fetch_jobs stamps enrich_source='api-lever'."""
        job = _lever_job()
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].enrich_source == "api-lever"

    @respx.mock
    async def test_fetch_jobs_uses_text_field_as_title(self) -> None:
        """Lever fetch_jobs uses 'text' field (not 'title') for the posting title."""
        job = _lever_job(text="Data Scientist")
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].title == "Data Scientist"

    @respx.mock
    async def test_fetch_jobs_uses_hosted_url_as_url(self) -> None:
        """Lever fetch_jobs uses 'hostedUrl' field as job URL."""
        job = _lever_job(hosted_url="https://jobs.lever.co/acme/abc")
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].url == "https://jobs.lever.co/acme/abc"

    @respx.mock
    async def test_fetch_jobs_extracts_location_from_categories(self) -> None:
        """Lever fetch_jobs extracts location from categories.location."""
        job = _lever_job(location="Seattle, WA")
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == "Seattle, WA"

    @respx.mock
    async def test_fetch_jobs_null_categories_defaults_to_empty_location(
        self,
    ) -> None:
        """Lever fetch_jobs defaults to empty string when categories is null."""
        job = _lever_job()
        job["categories"] = None
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == ""

    @respx.mock
    async def test_fetch_jobs_missing_location_in_categories_defaults_empty(
        self,
    ) -> None:
        """Lever fetch_jobs defaults to empty when categories.location is absent."""
        job = _lever_job()
        job["categories"] = {"team": "Engineering"}
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == ""

    @respx.mock
    async def test_fetch_jobs_parses_created_at_as_utc_datetime(self) -> None:
        """Lever fetch_jobs converts createdAt ms timestamp to UTC datetime."""
        ts_ms = 1_748_016_000_000
        job = _lever_job(created_at=ts_ms)
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is not None
        assert postings[0].posted_at.tzinfo == UTC

    @respx.mock
    async def test_fetch_jobs_invalid_created_at_becomes_none(self) -> None:
        """Lever fetch_jobs sets posted_at=None for invalid createdAt."""
        job = _lever_job()
        job["createdAt"] = "not-a-timestamp"
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is None

    @respx.mock
    async def test_fetch_jobs_missing_created_at_becomes_none(self) -> None:
        """Lever fetch_jobs sets posted_at=None when createdAt is absent."""
        job = _lever_job()
        job.pop("createdAt", None)
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is None

    @respx.mock
    async def test_fetch_jobs_combines_description_plain_list_headings_and_content(
        self,
    ) -> None:
        """Lever fetch_jobs includes descriptionPlain, headings, and content."""
        job = _lever_job(
            description_plain="Intro text. " + "D" * 100,
            lists=[{"text": "Requirements", "content": "<li>Python</li><li>Go</li>"}],
        )
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert "Intro text" in postings[0].jd_text
        assert "Requirements:" in postings[0].jd_text
        assert "Python" in postings[0].jd_text
        assert "Go" in postings[0].jd_text

    @respx.mock
    async def test_fetch_jobs_raises_ats_parse_error_on_non_list_response(
        self,
    ) -> None:
        """Lever fetch_jobs raises ATSParseError when response is not a list."""
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": []})
        )
        async with create_http_client() as client:
            with pytest.raises(ATSParseError):
                await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_skips_job_with_null_id(self) -> None:
        """Lever fetch_jobs skips jobs where id is null."""
        good = _lever_job(job_id="lever-good")
        bad = _lever_job(job_id="lever-null")
        bad["id"] = None
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[good, bad])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 1
        assert postings[0].canonical_id == "lever-good"

    @respx.mock
    async def test_fetch_jobs_isolates_malformed_job_continues_with_rest(
        self,
    ) -> None:
        """Lever fetch_jobs skips malformed job but returns other valid jobs."""
        good1 = _lever_job(job_id="good-1")
        bad = "not a dict"
        good2 = _lever_job(
            job_id="good-2", hosted_url="https://jobs.lever.co/acme/good-2"
        )
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[good1, bad, good2])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        ids = {p.canonical_id for p in postings}
        assert "good-1" in ids
        assert "good-2" in ids

    @respx.mock
    async def test_fetch_jobs_skips_job_with_blank_title(self) -> None:
        """Lever fetch_jobs skips jobs with blank text/title field."""
        job = _lever_job(text="")
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []

    @respx.mock
    async def test_fetch_jobs_raises_ats_fetch_error_on_http_error(self) -> None:
        """Lever fetch_jobs raises ATSFetchError on non-2xx HTTP status."""
        respx.get(LEVER_JOBS_URL).mock(return_value=httpx.Response(HTTP_500))
        async with create_http_client() as client:
            with pytest.raises(ATSFetchError):
                await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)

    @respx.mock
    async def test_fetch_jobs_uses_slug_as_company(self) -> None:
        """Lever fetch_jobs uses slug as company name."""
        job = _lever_job()
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].company == SLUG

    @respx.mock
    async def test_fetch_jobs_enriched_at_equals_discovered_at(self) -> None:
        """Lever fetch_jobs sets enriched_at equal to discovered_at."""
        job = _lever_job()
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].enriched_at == DISCOVERED_AT

    @respx.mock
    async def test_fetch_jobs_skips_blank_url(self) -> None:
        """Lever fetch_jobs skips jobs with blank hostedUrl."""
        job = _lever_job(hosted_url="")
        respx.get(LEVER_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json=[job])
        )
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 0

    @respx.mock
    async def test_fetch_jobs_returns_empty_for_empty_array(self) -> None:
        """Lever fetch_jobs returns empty list for empty response array."""
        respx.get(LEVER_JOBS_URL).mock(return_value=httpx.Response(HTTP_200, json=[]))
        async with create_http_client() as client:
            postings = await lever_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []


# ---------------------------------------------------------------------------
# Ashby — additional edge cases
# ---------------------------------------------------------------------------


class TestAshbyEdgeCases:
    """Additional edge case tests for Ashby adapter."""

    @respx.mock
    async def test_fetch_jobs_skips_blank_title(self) -> None:
        """Ashby fetch_jobs skips jobs with blank title."""
        job = _ashby_job(title="")
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 0

    @respx.mock
    async def test_fetch_jobs_skips_blank_url(self) -> None:
        """Ashby fetch_jobs skips jobs with blank jobUrl."""
        job = _ashby_job(job_url="")
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert len(postings) == 0

    @respx.mock
    async def test_fetch_jobs_returns_empty_for_empty_jobs_array(self) -> None:
        """Ashby fetch_jobs returns empty list when jobs array is empty."""
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": []})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings == []

    @respx.mock
    async def test_fetch_jobs_null_published_at_becomes_none(self) -> None:
        """Ashby fetch_jobs with publishedAt=null yields posted_at=None."""
        job = _ashby_job(published_at=None)
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].posted_at is None

    @respx.mock
    async def test_fetch_jobs_dict_location_extracts_name(self) -> None:
        """Ashby fetch_jobs handles dict-type location with name key."""
        job = _ashby_job(location={"name": "London, UK"})
        respx.get(ASHBY_JOBS_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": [job]})
        )
        async with create_http_client() as client:
            postings = await ashby_fetch_jobs(client, SLUG, discovered_at=DISCOVERED_AT)
        assert postings[0].location == "London, UK"
