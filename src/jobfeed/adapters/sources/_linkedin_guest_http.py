"""Async HTTP helpers for the LinkedIn guest (anonymous) job endpoints.

Owns every HTTP concern of the guest scraper: client construction (real-Chrome
``User-Agent``, optional proxy), URL builders for the two guest endpoints
(``seeMoreJobPostings/search`` and ``jobPosting/{id}``), raw param extraction
from a pasted LinkedIn search URL, and a never-raising ``fetch`` that retries
only request errors and 5xx. Callers classify the returned status (200 /
429 / 999 / 4xx) themselves; HTML parsing lives in ``_linkedin_guest_parse``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

_GUEST_API_BASE = "https://www.linkedin.com/jobs-guest/jobs/api"

# A real-Chrome UA: the guest endpoints answer 999 to obvious bot agents,
# so the polite "jobfeed/1.0" identifier used by the ATS adapters won't do.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_RETRY_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class SearchParams:
    """Raw query params pulled from a pasted LinkedIn search URL.

    Values are passthrough: ``f_tpr`` keeps the ``r<seconds>`` form (e.g.
    ``"r86400"``), never a JobSpy-style hours mapping.
    """

    keywords: str | None
    location: str | None
    f_tpr: str | None


@dataclass(frozen=True)
class GuestResponse:
    """Outcome of one guest GET.

    ``status`` is the HTTP status code, or ``0`` (with empty ``text``) as the
    sentinel for a request that never got a usable response — a request
    error or 5xx that persisted through every retry.
    """

    status: int
    text: str


_SENTINEL = GuestResponse(status=0, text="")


def create_client(proxies: str | None, timeout: float) -> httpx.AsyncClient:
    """Create the async HTTP client for guest LinkedIn requests.

    Args:
        proxies: Proxy URL (e.g. ``http://user:pass@host:port``) to route all
            requests through, or None/empty for a direct connection.
        timeout: Per-request read timeout in seconds. Connect timeout is
            fixed at 10s.

    Returns:
        Configured AsyncClient with a real-Chrome User-Agent and redirect
        following enabled.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        proxy=proxies if proxies else None,
    )


def search_url(
    keywords: str | None,
    location: str | None,
    f_tpr: str | None,
    start: int,
) -> str:
    """Build a guest search-endpoint URL.

    Args:
        keywords: Search keywords; omitted from the URL when None/empty.
        location: Location filter; omitted when None/empty.
        f_tpr: Raw time-posted-range value (e.g. ``"r86400"``); omitted when
            None/empty. Passed through verbatim — never an hours mapping.
        start: Pagination offset; always present.

    Returns:
        Fully encoded ``seeMoreJobPostings/search`` URL.
    """
    params: dict[str, str] = {}
    if keywords:
        params["keywords"] = keywords
    if location:
        params["location"] = location
    if f_tpr:
        params["f_TPR"] = f_tpr
    params["start"] = str(start)
    return f"{_GUEST_API_BASE}/seeMoreJobPostings/search?{urlencode(params)}"


def posting_url(job_id: str) -> str:
    """Build a guest posting-detail URL from a bare numeric job id.

    Args:
        job_id: Bare LinkedIn job id (e.g. ``"4012345678"``).

    Returns:
        ``jobPosting/{job_id}`` endpoint URL.
    """
    return f"{_GUEST_API_BASE}/jobPosting/{job_id}"


def parse_search_params(url: str) -> SearchParams:
    """Extract the raw guest-search params from a pasted LinkedIn search URL.

    Args:
        url: Any LinkedIn jobs search URL containing ``keywords`` /
            ``location`` / ``f_TPR`` query params.

    Returns:
        SearchParams with the raw (percent-decoded) first value of each param,
        or None for absent params. ``f_TPR`` stays the ``r<seconds>`` string.
        A malformed URL (one ``urlsplit`` rejects, e.g. a stray ``[`` in the
        authority) yields all-None, flowing into the caller's
        skip-with-warning path.
    """
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return SearchParams(keywords=None, location=None, f_tpr=None)
    return SearchParams(
        keywords=_first(query, "keywords"),
        location=_first(query, "location"),
        f_tpr=_first(query, "f_TPR"),
    )


def _first(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first value for a query key, or None when absent."""
    values = query.get(key)
    return values[0] if values else None


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = 2,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> GuestResponse:
    """GET a guest URL; never raises, retries only request errors and 5xx.

    Non-5xx statuses (including 429 and LinkedIn's 999 block code) are
    returned immediately for the caller to classify — burning retries on a
    rate-limit would only make the block worse.

    Args:
        client: Shared async HTTP client from :func:`create_client`.
        url: Target URL to fetch.
        retries: Extra attempts after the first on request errors / 5xx.
        sleep: Async backoff sleep, injectable so tests run without waiting.

    Returns:
        GuestResponse with the final status/body, or the ``(0, "")`` sentinel
        when every attempt hit a request error or 5xx.
    """
    for attempt in range(retries + 1):
        result = await _attempt(client, url)
        if result is not None:
            return result
        if attempt < retries:
            await sleep(_RETRY_BACKOFF_SECONDS)
    return _SENTINEL


async def _attempt(client: httpx.AsyncClient, url: str) -> GuestResponse | None:
    """Run one GET attempt; None signals a retryable failure.

    Retryable = ``httpx.RequestError`` (timeout, DNS, connect, read, plus
    ``TooManyRedirects``/``DecodingError``, which sit outside TransportError —
    LinkedIn blocks via authwall redirect loops and the client follows
    redirects) or a 5xx status. Anything else is final and returned as a
    GuestResponse.
    """
    try:
        response = await client.get(url)
    except httpx.RequestError:
        return None
    if response.is_server_error:
        return None
    return GuestResponse(status=response.status_code, text=response.text)


__all__ = [
    "GuestResponse",
    "SearchParams",
    "create_client",
    "fetch",
    "parse_search_params",
    "posting_url",
    "search_url",
]
