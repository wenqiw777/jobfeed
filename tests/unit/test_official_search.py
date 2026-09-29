"""Search output alone cannot establish official identity."""

import json
from unittest.mock import AsyncMock

import httpx

from jobfeed.adapters.sources.official_search import OfficialSearch, parse_official_page
from jobfeed.domain.models import LLMResponse
from tests.unit.test_intermediary_resolution import BODY, posting


def page(**changes):
    job = {
        "@type": "JobPosting",
        "title": "Software Engineer",
        "description": BODY,
        "hiringOrganization": {"name": "Acme"},
        "jobLocation": {
            "address": {"addressLocality": "Boston", "addressRegion": "MA"}
        },
    }
    job.update(changes)
    return '<script type="application/ld+json">' + json.dumps(job) + "</script>"


def test_official_jsonld_is_required_and_unrelated_identity_rejected():
    url = "https://boards.greenhouse.io/acme/jobs/123"
    assert parse_official_page(url, page()).company == "Acme"
    assert parse_official_page(url, "Please sign in") is None
    assert parse_official_page("https://dice.com/job-detail/1", page()) is None
    assert parse_official_page(url, page(validThrough="2020-01-01")) is None


async def test_only_ats_urls_are_fetched_and_verified():
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "urls": [
                    "http://127.0.0.1/secret",
                    "https://dice.com/job-detail/1",
                    "https://boards.greenhouse.io/acme/jobs/123",
                ]
            }
        ),
        model="mock",
        input_tokens=1,
        output_tokens=2,
        cost_usd=0.01,
    )
    requests = []

    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=page())

    store = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await OfficialSearch(llm, store, client=client)(posting(), "run")
    assert len(results) == 1
    assert requests == ["https://boards.greenhouse.io/acme/jobs/123"]
    store.record_llm_usage_with_cost.assert_awaited_once()
    store.set_state.assert_awaited_once()


async def test_requisition_uses_observed_employer_route_and_collapses_url_aliases():
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        content=json.dumps(
            {
                "urls": [],
                "employer": "Acme",
                "requisition_id": "R-123",
                "evidence_url": "https://careers.acme.com/jobs/123",
            }
        ),
        model="mock",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.01,
    )
    store = AsyncMock()
    store.employer_ats_urls.return_value = [
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Boston/Other_R-999",
        "https://acme.wd5.myworkdayjobs.com/External/job/Other_R-998",
    ]
    requested = []

    async def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, text=page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await OfficialSearch(llm, store, client=client)(posting(), "run")
    assert len(results) == 1
    assert len(requested) == 1
    assert results[0].canonical_id == "workday:acme:R-123"
    store.employer_ats_urls.assert_awaited_once_with("Acme")
