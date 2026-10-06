"""Dedupe must never spend the user's LinkedIn browser session."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from jobfeed.adapters.sources import _linkedin_application as module
from jobfeed.adapters.sources import _linkedin_guest_shared as shared
from jobfeed.adapters.sources._linkedin_guest_http import posting_url
from jobfeed.adapters.sources._linkedin_guest_shared import shared_fetch
from jobfeed.adapters.sources.application_route_browser import (
    ChromeApplicationPageReader,
)
from jobfeed.adapters.sources.application_routes import ApplicationRouteResolver
from jobfeed.application_resolution_wiring import build_application_resolver
from jobfeed.domain.application_route import ApplicationPageReadError
from tests.unit.test_application_routes import (
    ATS,
    BODY,
    WRAPPER,
    page,
    posting,
    resolve,
)

LINKEDIN = "https://www.linkedin.com/jobs/view/123"


async def test_existing_linkedin_id_and_jd_need_no_network():
    browser = AsyncMock()
    outcome, calls = await resolve(
        {}, posting(apply_url=LINKEDIN), chrome_reader=browser
    )
    assert outcome.status == "unresolved"
    assert outcome.reason == "linkedin_existing_data"
    assert calls == []
    browser.assert_not_awaited()


async def test_redirect_to_linkedin_does_not_open_or_http_fetch_the_page():
    browser = AsyncMock()
    outcome, calls = await resolve(
        {WRAPPER: httpx.Response(302, headers={"location": LINKEDIN})},
        chrome_reader=browser,
    )
    assert outcome.reason == "linkedin_existing_data"
    assert calls == [WRAPPER]
    browser.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [LINKEDIN, "https://uk.linkedin.com/jobs/view/123", "https://linkedin.com/login"],
)
async def test_browser_boundary_rejects_linkedin_before_bridge_dispatch(url):
    bridge = AsyncMock()
    with pytest.raises(ApplicationPageReadError, match="linkedin_browser_disabled"):
        await ChromeApplicationPageReader(bridge)(posting(), url)
    bridge.run_scan.assert_not_awaited()


async def test_saved_native_id_reused_even_when_incoming_jd_missing():

    store = AsyncMock()
    saved = posting(jd_text=None, apply_url=None)
    store.get_jobs_by_canonical_ids.return_value = {"123": saved}
    browser = AsyncMock()
    resolver = build_application_resolver(browser, store=store)
    outcome = await resolver(posting(jd_text=None, apply_url=LINKEDIN))
    assert outcome.reason == "linkedin_existing_data"
    browser.run_scan.assert_not_awaited()
    store.get_jobs_by_canonical_ids.assert_awaited_once_with(
        platform="linkedin_guest", canonical_ids=["123"]
    )


async def test_missing_data_uses_anonymous_guest_once_without_browser(monkeypatch):

    calls = []

    async def handler(request):
        calls.append(str(request.url))
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        assert str(request.url) == posting_url("123")
        return httpx.Response(
            200, text='<div class="show-more-less-html__markup">Guest JD</div>'
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # Scan/enrichment and dedupe share the very same Guest cache/queue.
    async with client:
        await shared_fetch(client, posting_url("123"))
    monkeypatch.setattr(
        module,
        "create_client",
        lambda *_: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    reader = module.LinkedInApplicationReader()
    result = await reader(posting(jd_text=None, apply_url=LINKEDIN), LINKEDIN)
    assert result.jd_text == "Guest JD"
    assert result.apply_url is None
    assert len(calls) == 1


@pytest.mark.parametrize("status", [403, 429, 999, 404])
async def test_guest_failure_never_falls_back_to_browser(monkeypatch, status):

    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status)

    monkeypatch.setattr(
        module,
        "create_client",
        lambda *_: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    browser = AsyncMock()
    result = await ApplicationRouteResolver(chrome_reader=browser)(
        posting(jd_text=None, apply_url=LINKEDIN)
    )
    assert result.reason == "linkedin_guest_unavailable"
    assert len(calls) == 1
    browser.assert_not_awaited()


async def test_shared_guest_queue_coalesces_concurrent_requests_and_cools_down():

    calls = []

    async def handler(request):
        calls.append(str(request.url))
        await asyncio.sleep(0)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await asyncio.gather(
            *[shared_fetch(client, posting_url("123")) for _ in range(5)]
        )
        other = await shared_fetch(client, posting_url("456"))
    assert [r.status for r in results] == [429] * 5
    assert other.status == httpx.codes.TOO_MANY_REQUESTS
    assert len(calls) == 1


async def test_guest_external_link_reaches_verified_ats_without_browser(monkeypatch):

    async def handler(request):
        assert str(request.url) == posting_url("123")
        return httpx.Response(
            200,
            text=(
                f'<div class="show-more-less-html__markup">{BODY}</div>'
                f'<a class="apply-button" href="{ATS}">Apply</a>'
            ),
        )

    monkeypatch.setattr(
        module,
        "create_client",
        lambda *_: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    browser = AsyncMock()
    result, calls = await resolve(
        {ATS: page()}, posting(jd_text=None, apply_url=LINKEDIN), chrome_reader=browser
    )
    assert result.status == "resolved"
    assert result.ats_url == ATS
    assert calls == [ATS]
    browser.assert_not_awaited()


async def test_guest_queue_paces_different_jobs_without_caching_search(monkeypatch):

    waits = []
    calls = []
    monkeypatch.setattr(shared, "monotonic", lambda: 100.0)

    async def sleep(seconds):
        waits.append(seconds)

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="Fixture")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for url in [
            posting_url("1"),
            posting_url("1"),
            posting_url("2"),
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        ]:
            await shared_fetch(client, url, sleep=sleep)
    assert calls == [
        posting_url("1"),
        posting_url("2"),
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
    ]
    assert waits == [1.0, 1.0, 1.0]
