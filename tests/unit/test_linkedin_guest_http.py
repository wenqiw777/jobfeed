"""Unit tests for the LinkedIn guest HTTP helper module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jobfeed.adapters.sources._linkedin_guest_http import (
    GuestResponse,
    SearchParams,
    create_client,
    fetch,
    parse_search_params,
    posting_url,
    search_url,
)

HTTP_200 = 200
HTTP_404 = 404
HTTP_429 = 429
HTTP_503 = 503
HTTP_999 = 999

RETRY_THEN_SUCCESS_ATTEMPTS = 2  # 1 failed + 1 successful retry
RETRIES_2_TOTAL_ATTEMPTS = 3  # retries=2 -> 1 initial + 2 retries

CUSTOM_TIMEOUT = 15.0

PASTED_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search"
    "?keywords=Machine%20Learning%20Engineer"
    "&location=United%20States&f_TPR=r86400&position=1&pageNum=0"
)


def _recording_sleep(delays: list[float]) -> Callable[[float], Awaitable[None]]:
    """Build an async sleep stub that records delays without sleeping."""

    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep


# ---------------------------------------------------------------------------
# create_client
# ---------------------------------------------------------------------------


def test_create_client_returns_async_client() -> None:
    """create_client returns an httpx.AsyncClient."""
    client = create_client(None, CUSTOM_TIMEOUT)
    assert isinstance(client, httpx.AsyncClient)


def test_create_client_sets_chrome_user_agent() -> None:
    """The User-Agent is Chrome-like, not the polite bot identifier."""
    client = create_client(None, CUSTOM_TIMEOUT)
    user_agent = client.headers["user-agent"]
    assert "Chrome/" in user_agent
    assert user_agent != "jobfeed/1.0"


def test_create_client_follows_redirects() -> None:
    """Redirect following is enabled."""
    client = create_client(None, CUSTOM_TIMEOUT)
    assert client.follow_redirects is True


def test_create_client_applies_timeout() -> None:
    """The given read timeout is applied to the client."""
    client = create_client(None, CUSTOM_TIMEOUT)
    assert client.timeout.read == CUSTOM_TIMEOUT


def test_create_client_no_proxy_when_none() -> None:
    """proxies=None mounts no proxy transport."""
    client = create_client(None, CUSTOM_TIMEOUT)
    assert client._mounts == {}


def test_create_client_no_proxy_when_empty_string() -> None:
    """proxies='' is treated the same as no proxy."""
    client = create_client("", CUSTOM_TIMEOUT)
    assert client._mounts == {}


def test_create_client_passes_proxy_to_httpx() -> None:
    """A non-empty proxies string is handed to httpx as the client proxy."""
    client = create_client("http://u:p@h:1", CUSTOM_TIMEOUT)
    (transport,) = client._mounts.values()
    proxy_url = transport._pool._proxy_url
    assert str(proxy_url.origin) == "http://h:1"


# ---------------------------------------------------------------------------
# search_url / posting_url
# ---------------------------------------------------------------------------


def test_search_url_includes_all_params() -> None:
    """All four query params appear, URL-encoded, on the guest search path."""
    url = search_url("software engineer", "United States", "r86400", 25)
    split = urlsplit(url)
    assert split.netloc == "www.linkedin.com"
    assert split.path == "/jobs-guest/jobs/api/seeMoreJobPostings/search"
    assert " " not in url
    params = parse_qs(split.query)
    assert params["keywords"] == ["software engineer"]
    assert params["location"] == ["United States"]
    assert params["f_TPR"] == ["r86400"]
    assert params["start"] == ["25"]


def test_search_url_omits_none_and_empty_params() -> None:
    """None/empty keywords, location, f_tpr are omitted; start is always present."""
    url = search_url(None, "", None, 0)
    params = parse_qs(urlsplit(url).query)
    assert "keywords" not in params
    assert "location" not in params
    assert "f_TPR" not in params
    assert params["start"] == ["0"]


def test_posting_url_uses_bare_job_id() -> None:
    """posting_url builds the guest jobPosting endpoint from a bare id."""
    url = posting_url("123")
    assert url == "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/123"


# ---------------------------------------------------------------------------
# parse_search_params
# ---------------------------------------------------------------------------


def test_parse_search_params_extracts_raw_values() -> None:
    """Raw keywords/location/f_TPR come back decoded but otherwise untouched."""
    params = parse_search_params(PASTED_SEARCH_URL)
    assert params == SearchParams(
        keywords="Machine Learning Engineer",
        location="United States",
        f_tpr="r86400",
    )


def test_parse_search_params_keeps_f_tpr_as_seconds_string() -> None:
    """f_TPR stays the raw r<seconds> string, never a JobSpy hours mapping."""
    params = parse_search_params("https://www.linkedin.com/jobs/search?f_TPR=r86400")
    assert params.f_tpr == "r86400"


def test_parse_search_params_absent_params_are_none() -> None:
    """A URL without the params yields None fields."""
    params = parse_search_params("https://www.linkedin.com/jobs/search?position=1")
    assert params == SearchParams(keywords=None, location=None, f_tpr=None)


def test_parse_search_params_malformed_url_returns_all_none() -> None:
    """A malformed pasted URL (urlsplit ValueError) yields all-None, no raise."""
    params = parse_search_params("https://[bad/url")
    assert params == SearchParams(keywords=None, location=None, f_tpr=None)


def test_parse_search_params_round_trips_search_url() -> None:
    """parse_search_params recovers exactly what search_url encoded."""
    url = search_url("staff engineer", "New York, NY", "r604800", 50)
    params = parse_search_params(url)
    assert params == SearchParams(
        keywords="staff engineer",
        location="New York, NY",
        f_tpr="r604800",
    )


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


async def test_fetch_returns_success_response() -> None:
    """A 200 comes back as GuestResponse(status=200, text=body) without raising."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTP_200, text="<html>cards</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(client, "https://www.linkedin.com/x")
    assert result == GuestResponse(status=HTTP_200, text="<html>cards</html>")


async def test_fetch_retries_503_then_succeeds() -> None:
    """A 503 is retried (with backoff) and the follow-up 200 is returned."""
    calls: list[int] = []
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(HTTP_503, text="oops")
        return httpx.Response(HTTP_200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(
            client, "https://www.linkedin.com/x", sleep=_recording_sleep(delays)
        )
    assert result == GuestResponse(status=HTTP_200, text="ok")
    assert len(calls) == RETRY_THEN_SUCCESS_ATTEMPTS
    assert len(delays) == 1


async def test_fetch_retries_transport_error_then_succeeds() -> None:
    """A transport error is retried (with backoff) and the follow-up 200 returned."""
    calls: list[int] = []
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(HTTP_200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(
            client, "https://www.linkedin.com/x", sleep=_recording_sleep(delays)
        )
    assert result == GuestResponse(status=HTTP_200, text="ok")
    assert len(calls) == RETRY_THEN_SUCCESS_ATTEMPTS
    assert len(delays) == 1


async def test_fetch_redirect_loop_returns_sentinel() -> None:
    """An authwall-style redirect loop yields the (0, '') sentinel, never a raise.

    httpx raises TooManyRedirects, which is a RequestError but NOT a
    TransportError — fetch must still swallow it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": str(request.url)})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        result = await fetch(
            client, "https://www.linkedin.com/x", sleep=_recording_sleep([])
        )
    assert result == GuestResponse(status=0, text="")


async def test_fetch_persistent_5xx_returns_sentinel() -> None:
    """Exhausting retries on 5xx yields the (0, '') sentinel, never a raise."""
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(HTTP_503, text="down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(
            client, "https://www.linkedin.com/x", retries=2, sleep=_recording_sleep([])
        )
    assert result == GuestResponse(status=0, text="")
    assert len(calls) == RETRIES_2_TOTAL_ATTEMPTS


async def test_fetch_persistent_transport_error_returns_sentinel() -> None:
    """Exhausting retries on transport errors yields the sentinel, never a raise."""
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(
            client, "https://www.linkedin.com/x", retries=2, sleep=_recording_sleep([])
        )
    assert result == GuestResponse(status=0, text="")
    assert len(calls) == RETRIES_2_TOTAL_ATTEMPTS


async def test_fetch_does_not_retry_429() -> None:
    """A 429 is returned as-is for the caller to classify; no retry burned."""
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(HTTP_429, text="slow down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(client, "https://www.linkedin.com/x")
    assert result == GuestResponse(status=HTTP_429, text="slow down")
    assert len(calls) == 1


@pytest.mark.parametrize("status", [HTTP_404, HTTP_999])
async def test_fetch_does_not_retry_4xx_or_999(status: int) -> None:
    """Non-5xx statuses (404, LinkedIn's 999) pass through on the first attempt."""
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, text="blocked")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(client, "https://www.linkedin.com/x")
    assert result == GuestResponse(status=status, text="blocked")
    assert len(calls) == 1


async def test_fetch_retries_zero_fails_after_single_attempt() -> None:
    """retries=0 means one attempt: failure returns the sentinel with no sleep."""
    calls: list[int] = []
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch(
            client,
            "https://www.linkedin.com/x",
            retries=0,
            sleep=_recording_sleep(delays),
        )
    assert result == GuestResponse(status=0, text="")
    assert len(calls) == 1
    assert delays == []
