"""Shared async HTTP client infrastructure for ATS vendor adapters."""

from __future__ import annotations

import html
from typing import Any

import httpx
from bs4 import BeautifulSoup

_USER_AGENT = "jobfeed/1.0"

# HTTP status codes with definitive "board is dead" semantics
_DEAD_BOARD_STATUSES = frozenset({404, 410})


class ATSFetchError(Exception):
    """Base error for ATS HTTP fetch failures.

    Carries per-company context so callers never have to infer it from URLs.
    """

    def __init__(
        self,
        message: str,
        *,
        slug: str,
        vendor: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.slug = slug
        self.vendor = vendor
        self.status_code = status_code


class ATSParseError(ATSFetchError):
    """Raised when the top-level JSON response schema is malformed."""


class ProbeNetworkError(ATSFetchError):
    """Raised on network-level probe failures (timeout, DNS resolution)."""


class ProbeIndeterminateError(ATSFetchError):
    """Raised when a probe received a response but cannot safely classify the board."""


def create_http_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """Create a configured async HTTP client for ATS vendor requests.

    Args:
        timeout: Per-request read timeout in seconds. Connect timeout is fixed at 10s.

    Returns:
        Configured AsyncClient with polite bot identification headers.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    )


async def fetch_json(  # noqa: PLR0913
    client: httpx.AsyncClient,
    url: str,
    *,
    slug: str,
    vendor: str,
    timeout: float | None = None,
    retries: int = 1,
) -> dict[str, Any] | list[Any]:
    """GET a URL and return parsed JSON.

    Args:
        client: Shared async HTTP client.
        url: Target URL to fetch.
        slug: Company slug for error context.
        vendor: ATS vendor name for error context.
        timeout: Optional per-request override in seconds.
        retries: Number of extra attempts on transport errors (timeout/connect/
            read/DNS). A slug that times out one moment often answers on the
            next when a vendor is intermittently degraded. HTTP status errors
            (4xx/5xx) and JSON-decode failures are never retried.

    Returns:
        Parsed JSON as dict or list.

    Raises:
        ATSFetchError: On non-2xx HTTP status or network/timeout errors.
        ATSParseError: On JSON decode failure after a successful response.
    """
    request_kwargs: dict[str, Any] = {}
    if timeout is not None:
        request_kwargs["timeout"] = timeout

    last_exc: BaseException | None = None
    for _ in range(retries + 1):
        try:
            response = await client.get(url, **request_kwargs)
            break
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
    else:
        raise ATSFetchError(
            f"Network error fetching {vendor}/{slug}: {last_exc}",
            slug=slug,
            vendor=vendor,
            status_code=None,
        ) from last_exc

    if not response.is_success:
        raise ATSFetchError(
            f"HTTP {response.status_code} from {vendor}/{slug}",
            slug=slug,
            vendor=vendor,
            status_code=response.status_code,
        )

    try:
        return response.json()  # type: ignore[no-any-return]
    except Exception as exc:
        raise ATSParseError(
            f"JSON decode failed for {vendor}/{slug}: {exc}",
            slug=slug,
            vendor=vendor,
            status_code=response.status_code,
        ) from exc


async def probe_url(  # noqa: PLR0913
    client: httpx.AsyncClient,
    url: str,
    *,
    slug: str,
    vendor: str,
    timeout: float = 5.0,
    method: str = "HEAD",
) -> bool:
    """Check whether an ATS board URL is live.

    Args:
        client: Shared async HTTP client.
        url: URL to probe.
        slug: Company slug for error context.
        vendor: ATS vendor name for error context.
        timeout: Probe-specific timeout in seconds.
        method: HTTP method to use (default: HEAD).

    Returns:
        True if the board is live (2xx), False if definitively dead (404/410).

    Raises:
        ProbeNetworkError: On network-level failures (timeout, DNS).
        ProbeIndeterminateError: On responses that reached the server but
            cannot safely classify the board (401, 403, 405, 429, 5xx, etc.).
    """
    try:
        response = await client.request(method, url, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProbeNetworkError(
            f"Network error probing {vendor}/{slug}: {exc}",
            slug=slug,
            vendor=vendor,
            status_code=None,
        ) from exc

    if response.is_success:
        return True

    if response.status_code in _DEAD_BOARD_STATUSES:
        return False

    raise ProbeIndeterminateError(
        f"Indeterminate probe status {response.status_code} for {vendor}/{slug}",
        slug=slug,
        vendor=vendor,
        status_code=response.status_code,
    )


def html_to_text(html_content: str) -> str:
    """Extract plain text from an HTML string.

    Unescapes HTML entities before parsing, then uses BeautifulSoup to extract
    readable text with newline separators.

    Args:
        html_content: Raw HTML string, possibly containing entities like &amp;.

    Returns:
        Plain text with whitespace normalized.
    """
    unescaped = html.unescape(html_content)
    soup = BeautifulSoup(unescaped, "html.parser")
    return soup.get_text(separator="\n", strip=True)


__all__ = [
    "ATSFetchError",
    "ATSParseError",
    "ProbeIndeterminateError",
    "ProbeNetworkError",
    "create_http_client",
    "fetch_json",
    "html_to_text",
    "probe_url",
]
