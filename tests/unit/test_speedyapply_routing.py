"""Unit tests for SpeedyApply apply-URL routing + per-vendor JD fetch.

All HTTP is mocked with respx — no real network. Covers the greenhouse single-job
GET, the Ashby/Lever fetch-once + match-by-canonical_id + slug_cache behavior,
the SmartRecruiters/iCIMS/Workday JD helpers, the unrouted/not-found labels,
per-row error containment, and the SimpleSource protocol shape.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
import respx

from jobfeed.adapters.sources import _speedyapply_routing as routing
from jobfeed.adapters.sources._ats_workday import _build_cxs_url
from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.config import SourcesSpeedyApplyConfig
from jobfeed.domain.models import JobPosting
from jobfeed.observability import get_logger
from jobfeed.ports.source import SimpleSource

TIMEOUT = 30.0

# Long enough JD text that assess_quality returns a non-MISSING band.
_LONG_JD = "Engineering role. " * 30

_ASHBY_UUID = "95ae3b0a-a061-4323-926a-7fa308b59387"
_LEVER_UUID = "86db99fb-95c2-4369-b54f-b0d21fa59ba0"


def _ashby_payload(uuid: str, *, slug: str = "ironcladhq") -> dict[str, object]:
    return {
        "jobs": [
            {
                "id": uuid,
                "title": "SWE Intern",
                "jobUrl": f"https://jobs.ashbyhq.com/{slug}/{uuid}",
                "descriptionPlain": _LONG_JD,
                "location": "Remote",
            }
        ]
    }


def _lever_payload(uuid: str) -> list[dict[str, object]]:
    return [
        {
            "id": uuid,
            "text": "SWE Intern",
            "hostedUrl": f"https://jobs.lever.co/zushealth/{uuid}",
            "descriptionPlain": _LONG_JD,
            "categories": {"location": "NYC"},
        }
    ]


# ---------------------------------------------------------------------------
# greenhouse — single-job GET (NOT the whole board)
# ---------------------------------------------------------------------------


@respx.mock
async def test_greenhouse_uses_single_job_endpoint() -> None:
    """Greenhouse routing hits /jobs/<id>?content=true and returns its JD."""
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/777?content=true"
    route = respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 777,
                "title": "SWE Intern",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/777",
                "content": f"<p>{_LONG_JD}</p>",
                "location": {"name": "SF"},
            },
        )
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            "https://job-boards.greenhouse.io/acme/jobs/777",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert route.called
    assert result.enrich_source == "speedyapply-greenhouse"
    assert "Engineering role" in result.jd_text


# ---------------------------------------------------------------------------
# ashby / lever — fetch board once, match by canonical_id, cache per slug
# ---------------------------------------------------------------------------


@respx.mock
async def test_ashby_matches_by_canonical_id() -> None:
    """Ashby routing fetches the board and matches the target UUID."""
    respx.get(
        "https://api.ashbyhq.com/posting-api/job-board/ironcladhq"
        "?includeCompensation=true"
    ).mock(return_value=httpx.Response(200, json=_ashby_payload(_ASHBY_UUID)))
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            f"https://jobs.ashbyhq.com/ironcladhq/{_ASHBY_UUID}",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.enrich_source == "speedyapply-ashby"
    assert "Engineering role" in result.jd_text


@respx.mock
async def test_ashby_slug_cache_fetches_board_once_for_two_rows() -> None:
    """Two rows from the same Ashby slug fetch the board once (slug_cache)."""
    other_uuid = "11111111-2222-3333-4444-555555555555"
    payload = _ashby_payload(_ASHBY_UUID)
    payload["jobs"] = [
        *payload["jobs"],  # type: ignore[list-item]
        {
            "id": other_uuid,
            "title": "Backend Intern",
            "jobUrl": f"https://jobs.ashbyhq.com/ironcladhq/{other_uuid}",
            "descriptionPlain": _LONG_JD,
            "location": "Remote",
        },
    ]
    route = respx.get(
        "https://api.ashbyhq.com/posting-api/job-board/ironcladhq"
        "?includeCompensation=true"
    ).mock(return_value=httpx.Response(200, json=payload))

    slug_cache: routing.SlugCache = {}
    async with create_http_client() as client:
        first = await routing.route_and_fetch(
            client,
            f"https://jobs.ashbyhq.com/ironcladhq/{_ASHBY_UUID}",
            slug_cache=slug_cache,
            timeout=TIMEOUT,
        )
        second = await routing.route_and_fetch(
            client,
            f"https://jobs.ashbyhq.com/ironcladhq/{other_uuid}",
            slug_cache=slug_cache,
            timeout=TIMEOUT,
        )
    assert route.call_count == 1
    assert first.enrich_source == "speedyapply-ashby"
    assert second.enrich_source == "speedyapply-ashby"
    assert ("ashby", "ironcladhq") in slug_cache


@respx.mock
async def test_ashby_not_found_when_id_absent_from_board() -> None:
    """A target UUID missing from the board yields speedyapply-notfound."""
    respx.get(
        "https://api.ashbyhq.com/posting-api/job-board/ironcladhq"
        "?includeCompensation=true"
    ).mock(return_value=httpx.Response(200, json=_ashby_payload(_ASHBY_UUID)))
    missing = "deadbeef-0000-0000-0000-000000000000"
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            f"https://jobs.ashbyhq.com/ironcladhq/{missing}",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.jd_text == ""
    assert result.enrich_source == "speedyapply-notfound"


@respx.mock
async def test_lever_matches_by_canonical_id() -> None:
    """Lever routing fetches the board and matches the target UUID."""
    respx.get("https://api.lever.co/v0/postings/zushealth?mode=json").mock(
        return_value=httpx.Response(200, json=_lever_payload(_LEVER_UUID))
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            f"https://jobs.lever.co/zushealth/{_LEVER_UUID}/apply",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.enrich_source == "speedyapply-lever"
    assert "Engineering role" in result.jd_text


# ---------------------------------------------------------------------------
# smartrecruiters / workday (JSON) and icims (HTML via fetch_text)
# ---------------------------------------------------------------------------


@respx.mock
async def test_smartrecruiters_concats_sections() -> None:
    """SmartRecruiters JD concatenates the named sections, HTML-stripped."""
    respx.get(
        "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings/744000123"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "jobAd": {
                    "sections": {
                        "companyDescription": {"text": "<p>About ServiceNow</p>"},
                        "jobDescription": {"text": "<p>Build features</p>"},
                        "qualifications": {"text": "<p>Python</p>"},
                    }
                }
            },
        )
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            "https://jobs.smartrecruiters.com/ServiceNow/744000123-assoc-swe",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert result.enrich_source == "speedyapply-smartrecruiters"
    assert "About ServiceNow" in result.jd_text
    assert "Build features" in result.jd_text
    assert "Python" in result.jd_text
    assert "<p>" not in result.jd_text


@respx.mock
async def test_workday_extracts_job_description() -> None:
    """Workday two-step: GET apply HTML then CXS endpoint, strips HTML tags."""
    apply_url = (
        "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/Fort-Belvoir-VA/"
        "Data-Engineer-Intern_R-00180867"
    )
    cxs = (
        "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/external/job/"
        "Fort-Belvoir-VA/Data-Engineer-Intern_R-00180867"
    )
    _token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    _html = (
        "<html><body><script>var c = {"
        f'token: "{_token}", postingAvailable: true,'
        "};</script></body></html>"
    )
    respx.get(apply_url).mock(return_value=httpx.Response(200, text=_html))
    respx.get(cxs).mock(
        return_value=httpx.Response(
            200, json={"jobPostingInfo": {"jobDescription": "<p>Clearance role</p>"}}
        )
    )
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client, apply_url, slug_cache={}, timeout=TIMEOUT
        )
    assert result.enrich_source == "speedyapply-workday"
    assert "Clearance role" in result.jd_text
    assert "<p>" not in result.jd_text


@respx.mock
async def test_icims_uses_fetch_text_and_json_ld() -> None:
    """iCIMS fetches raw HTML (?in_iframe=1) and extracts JSON-LD description."""
    iframe = "https://careers-kinaxis.icims.com/jobs/34701/co-op-intern/job?in_iframe=1"
    html_body = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "JobPosting", "description": "<p>Co-op JD body</p>"}'
        "</script></head><body></body></html>"
    )
    route = respx.get(iframe).mock(return_value=httpx.Response(200, text=html_body))
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client,
            "https://careers-kinaxis.icims.com/jobs/34701/co-op-intern/job",
            slug_cache={},
            timeout=TIMEOUT,
        )
    assert route.called
    assert result.enrich_source == "speedyapply-icims"
    assert "Co-op JD body" in result.jd_text
    assert "<p>" not in result.jd_text


# ---------------------------------------------------------------------------
# unrouted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.uber.com/global/en/careers/list/159161",
        "https://lifeattiktok.com/search/123",
        "https://apply.workable.com/rokt/j/783A754DDB/",
        "http://acme.applytojob.com/apply/abc/Engineer",
    ],
)
@respx.mock
async def test_unrouted_hosts_return_empty(url: str) -> None:
    """Hosts with no vendor integration yield ('', speedyapply-unrouted)."""
    async with create_http_client() as client:
        result = await routing.route_and_fetch(
            client, url, slug_cache={}, timeout=TIMEOUT
        )
    assert result.jd_text == ""
    assert result.enrich_source == "speedyapply-unrouted"


# ---------------------------------------------------------------------------
# SpeedyApplySource end-to-end (in-source dedup + per-row error containment)
# ---------------------------------------------------------------------------


def _source(search_urls: list[str]) -> SpeedyApplySource:
    return SpeedyApplySource(
        client=create_http_client(),
        config=SourcesSpeedyApplyConfig(search_urls=search_urls, enabled=True),
        logger=get_logger(),
    )


def test_source_satisfies_simple_source_protocol() -> None:
    """SpeedyApplySource is a runtime SimpleSource."""
    assert isinstance(_source(["https://x/README.md"]), SimpleSource)


@respx.mock
async def test_source_contains_one_row_failure_keeps_others() -> None:
    """One row's vendor fetch failing does not drop the other rows."""
    list_url = "https://lists.test/README.md"
    md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a><strong>Acme</strong></a> | SWE Intern | SF | <a href="https://job-boards.greenhouse.io/acme/jobs/1"><img src="x"/></a> | 2d |
| <a><strong>Beta</strong></a> | Data Intern | NY | <a href="https://job-boards.greenhouse.io/beta/jobs/2"><img src="x"/></a> | 3d |
| <a><strong>Gamma</strong></a> | ML Intern | LA | <a href="https://uber.com/careers/9"><img src="x"/></a> | 4d |
"""
    respx.get(list_url).mock(return_value=httpx.Response(200, text=md))
    # Acme succeeds; Beta's vendor fetch fails (500); Gamma is unrouted.
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1?content=true"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1,
                "title": "SWE Intern",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                "content": f"<p>{_LONG_JD}</p>",
                "location": {"name": "SF"},
            },
        )
    )
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/beta/jobs/2?content=true"
    ).mock(return_value=httpx.Response(500))

    source = _source([list_url])
    postings = await source.fetch_jobs({})

    by_company = {p.company: p for p in postings}
    assert set(by_company) == {"Acme", "Beta", "Gamma"}
    acme_jd = by_company["Acme"].jd_text
    assert by_company["Acme"].enrich_source == "speedyapply-greenhouse"
    assert acme_jd and "Engineering role" in acme_jd
    assert by_company["Beta"].enrich_source == "speedyapply-error"
    assert by_company["Beta"].jd_text is None
    assert by_company["Gamma"].enrich_source == "speedyapply-unrouted"
    # enriched_at is stamped only when a JD was actually fetched (Acme), and
    # left None for the error/unrouted rows (consistency with ATS/JobSpy).
    assert by_company["Acme"].enriched_at == by_company["Acme"].discovered_at
    assert by_company["Beta"].enriched_at is None
    assert by_company["Gamma"].enriched_at is None


@respx.mock
async def test_source_dedupes_by_canonical_id() -> None:
    """The same apply URL across two tables collapses to one posting."""
    list_url = "https://lists.test/README.md"
    md = """| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a><strong>Acme</strong></a> | SWE Intern | SF | <a href="https://uber.com/careers/9"><img src="x"/></a> | 2d |
| <a><strong>Acme</strong></a> | SWE Intern (dup) | SF | <a href="https://uber.com/careers/9"><img src="x"/></a> | 2d |
"""
    respx.get(list_url).mock(return_value=httpx.Response(200, text=md))
    source = _source([list_url])
    postings = await source.fetch_jobs({})
    assert len(postings) == 1
    assert postings[0].title == "SWE Intern"


def _ashby_posting(uuid: str, discovered_at: datetime) -> JobPosting:
    """A minimal Ashby JobPosting whose canonical_id == the URL UUID."""
    return JobPosting(
        platform="ashby",
        canonical_id=uuid,
        url=f"https://jobs.ashbyhq.com/acme/{uuid}",
        title="SWE Intern",
        company="Acme",
        location="Remote",
        discovered_at=discovered_at,
        jd_text=_LONG_JD,
    )


async def test_route_coalesces_concurrent_same_slug_board_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent same-slug Ashby routes coalesce onto ONE board fetch.

    The board stub yields (``await asyncio.sleep(0)``) between the cache
    miss-check and store so the two routes genuinely interleave — respx mocks
    resolve without suspending and cannot reproduce the race. Verified to fail
    (calls == 2) against the pre-fix await-before-store logic. Regression guard
    for the slug_cache stampede fix (cache the in-flight Task, not the list).

    Args:
        monkeypatch: Swaps the Ashby board fetch for a counting, yielding stub.
    """
    uuid_b = "11111111-2222-3333-4444-555555555555"
    calls = 0

    async def fake_board(
        *_args: object,
        discovered_at: datetime,
        **_kwargs: object,
    ) -> list[JobPosting]:
        # Variadic to stay call-compatible with ashby.fetch_jobs(client, slug,
        # *, discovered_at, timeout) without carrying unused named params.
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)  # yield: a stampede would double-count here
        return [
            _ashby_posting(_ASHBY_UUID, discovered_at),
            _ashby_posting(uuid_b, discovered_at),
        ]

    monkeypatch.setattr(routing.ashby, "fetch_jobs", fake_board)

    slug_cache: routing.SlugCache = {}
    async with create_http_client() as client:
        results = await asyncio.gather(
            routing.route_and_fetch(
                client,
                f"https://jobs.ashbyhq.com/acme/{_ASHBY_UUID}",
                slug_cache=slug_cache,
                timeout=TIMEOUT,
            ),
            routing.route_and_fetch(
                client,
                f"https://jobs.ashbyhq.com/acme/{uuid_b}",
                slug_cache=slug_cache,
                timeout=TIMEOUT,
            ),
        )

    assert calls == 1  # coalesced onto one in-flight Task, not stampeded
    assert [r.enrich_source for r in results] == [
        "speedyapply-ashby",
        "speedyapply-ashby",
    ]
    assert all(r.jd_text for r in results)


def test_workday_routes_with_and_without_locale_segment() -> None:
    """Workday URLs route to the CXS endpoint with OR without a locale segment.

    Regression guard for the Codex P2 finding: many Workday sites omit the
    ``<lang>`` segment, and those must still resolve the board correctly.
    """
    expected = (
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/CareerSite/job/REQ-1",
        "acme",
    )
    # No locale segment: .../<board>/job/<rest>
    assert (
        _build_cxs_url("https://acme.wd5.myworkdayjobs.com/CareerSite/job/REQ-1")
        == expected
    )
    # With locale segment: .../<lang>/<board>/job/<rest>
    assert (
        _build_cxs_url("https://acme.wd5.myworkdayjobs.com/en-US/CareerSite/job/REQ-1")
        == expected
    )
