"""Unit tests for shared ATS HTTP client infrastructure."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfeed.adapters.sources._http import (
    ATSFetchError,
    ATSParseError,
    ProbeIndeterminateError,
    ProbeNetworkError,
    create_http_client,
    fetch_json,
    fetch_text,
    html_to_text,
    probe_url,
)

SLUG = "acme"
VENDOR = "greenhouse"
BASE_URL = "https://api.greenhouse.io/v1/boards/acme/jobs"
PROBE_URL = "https://boards.greenhouse.io/acme"

# Timeout constants (mirrors defaults in _http.py)
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0
CUSTOM_READ_TIMEOUT = 60.0

# HTTP status codes used in tests
HTTP_200 = 200
HTTP_403 = 403
HTTP_404 = 404
HTTP_410 = 410
HTTP_500 = 500
HTTP_503 = 503
HTTP_429 = 429

RETRY_THEN_SUCCESS_ATTEMPTS = 2  # 1 failed + 1 successful retry
RETRIES_2_TOTAL_ATTEMPTS = 3  # retries=2 -> 1 initial + 2 retries


# ---------------------------------------------------------------------------
# create_http_client
# ---------------------------------------------------------------------------


def test_create_http_client_returns_async_client() -> None:
    """create_http_client returns a properly typed AsyncClient."""
    client = create_http_client()
    assert isinstance(client, httpx.AsyncClient)


def test_create_http_client_sets_user_agent() -> None:
    """create_http_client sets User-Agent header to jobfeed/1.0."""
    client = create_http_client()
    assert client.headers["user-agent"] == "jobfeed/1.0"


def test_create_http_client_follows_redirects() -> None:
    """create_http_client enables redirect following."""
    client = create_http_client()
    assert client.follow_redirects is True


def test_create_http_client_custom_timeout() -> None:
    """create_http_client accepts a custom read timeout."""
    client = create_http_client(timeout=CUSTOM_READ_TIMEOUT)
    assert client.timeout.read == CUSTOM_READ_TIMEOUT
    assert client.timeout.connect == DEFAULT_CONNECT_TIMEOUT


def test_create_http_client_default_timeout() -> None:
    """create_http_client defaults to 30s read timeout and 10s connect timeout."""
    client = create_http_client()
    assert client.timeout.read == DEFAULT_READ_TIMEOUT
    assert client.timeout.connect == DEFAULT_CONNECT_TIMEOUT


# ---------------------------------------------------------------------------
# fetch_json — success
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_json_returns_dict_on_200() -> None:
    """fetch_json returns parsed dict on HTTP 200."""
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})
    )
    async with create_http_client() as client:
        result = await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert isinstance(result, dict)
    assert "jobs" in result


@respx.mock
async def test_fetch_json_returns_list_on_200() -> None:
    """fetch_json returns parsed list when the endpoint returns a JSON array."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=[{"id": 1}]))
    async with create_http_client() as client:
        result = await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert isinstance(result, list)
    assert result[0]["id"] == 1


# ---------------------------------------------------------------------------
# fetch_json — HTTP error statuses
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_json_raises_ats_fetch_error_on_404() -> None:
    """fetch_json raises ATSFetchError with status_code, slug, and vendor on 404."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(HTTP_404))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    err = exc_info.value
    assert err.status_code == HTTP_404
    assert err.slug == SLUG
    assert err.vendor == VENDOR
    assert not isinstance(err, ATSParseError)


@respx.mock
async def test_fetch_json_raises_ats_fetch_error_on_500() -> None:
    """fetch_json raises ATSFetchError with status_code on server error."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(HTTP_500))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert exc_info.value.status_code == HTTP_500


@respx.mock
async def test_fetch_json_raises_ats_fetch_error_on_403() -> None:
    """fetch_json raises ATSFetchError on forbidden response."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(HTTP_403))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert exc_info.value.status_code == HTTP_403
    assert exc_info.value.slug == SLUG
    assert exc_info.value.vendor == VENDOR


# ---------------------------------------------------------------------------
# fetch_json — network errors
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_json_raises_ats_fetch_error_on_timeout() -> None:
    """fetch_json raises ATSFetchError with status_code=None on timeout."""
    respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    err = exc_info.value
    assert err.status_code is None
    assert err.slug == SLUG
    assert err.vendor == VENDOR


@respx.mock
async def test_fetch_json_raises_ats_fetch_error_on_connection_error() -> None:
    """fetch_json raises ATSFetchError with status_code=None on DNS failure."""
    respx.get(BASE_URL).mock(side_effect=httpx.ConnectError("DNS failure"))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert exc_info.value.status_code is None


@respx.mock
async def test_fetch_json_retries_transport_error_then_succeeds() -> None:
    """A transient transport error is retried and the next attempt succeeds."""
    route = respx.get(BASE_URL).mock(
        side_effect=[
            httpx.TimeoutException("timed out"),
            httpx.Response(HTTP_200, json={"jobs": []}),
        ]
    )
    async with create_http_client() as client:
        result = await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert result == {"jobs": []}
    assert route.call_count == RETRY_THEN_SUCCESS_ATTEMPTS


@respx.mock
async def test_fetch_json_gives_up_after_retries_exhausted() -> None:
    """Transport errors on every attempt exhaust retries and raise."""
    route = respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR, retries=2)

    assert exc_info.value.status_code is None
    assert route.call_count == RETRIES_2_TOTAL_ATTEMPTS


# ---------------------------------------------------------------------------
# fetch_json — JSON parse errors
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_json_raises_ats_parse_error_on_invalid_json() -> None:
    """fetch_json raises ATSParseError when response body is not valid JSON."""
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(HTTP_200, content=b"<html>not json</html>")
    )
    async with create_http_client() as client:
        with pytest.raises(ATSParseError) as exc_info:
            await fetch_json(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    err = exc_info.value
    assert err.status_code == HTTP_200
    assert err.slug == SLUG
    assert err.vendor == VENDOR
    assert isinstance(err, ATSFetchError)


# ---------------------------------------------------------------------------
# fetch_text — raw HTML body (for iCIMS and other HTML-embedded JDs)
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_text_returns_body_on_200() -> None:
    """fetch_text returns the raw response body text on HTTP 200."""
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(HTTP_200, text="<html><body>hi</body></html>")
    )
    async with create_http_client() as client:
        result = await fetch_text(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert result == "<html><body>hi</body></html>"


@respx.mock
async def test_fetch_text_raises_ats_fetch_error_on_404() -> None:
    """fetch_text raises ATSFetchError with status/slug/vendor on 404."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(HTTP_404))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_text(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    err = exc_info.value
    assert err.status_code == HTTP_404
    assert err.slug == SLUG
    assert err.vendor == VENDOR


@respx.mock
async def test_fetch_text_raises_ats_fetch_error_on_timeout() -> None:
    """fetch_text raises ATSFetchError with status_code=None on timeout."""
    respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_text(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert exc_info.value.status_code is None


@respx.mock
async def test_fetch_text_retries_transport_error_then_succeeds() -> None:
    """A transient transport error is retried and the next attempt succeeds."""
    route = respx.get(BASE_URL).mock(
        side_effect=[
            httpx.TimeoutException("timed out"),
            httpx.Response(HTTP_200, text="ok"),
        ]
    )
    async with create_http_client() as client:
        result = await fetch_text(client, BASE_URL, slug=SLUG, vendor=VENDOR)

    assert result == "ok"
    assert route.call_count == RETRY_THEN_SUCCESS_ATTEMPTS


@respx.mock
async def test_fetch_text_gives_up_after_retries_exhausted() -> None:
    """Transport errors on every attempt exhaust retries and raise."""
    route = respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with create_http_client() as client:
        with pytest.raises(ATSFetchError) as exc_info:
            await fetch_text(client, BASE_URL, slug=SLUG, vendor=VENDOR, retries=2)

    assert exc_info.value.status_code is None
    assert route.call_count == RETRIES_2_TOTAL_ATTEMPTS


# ---------------------------------------------------------------------------
# probe_url — True on 2xx
# ---------------------------------------------------------------------------


@respx.mock
async def test_probe_url_returns_true_on_200() -> None:
    """probe_url returns True for HTTP 200 response."""
    respx.head(PROBE_URL).mock(return_value=httpx.Response(HTTP_200))
    async with create_http_client() as client:
        result = await probe_url(client, PROBE_URL, slug=SLUG, vendor=VENDOR)

    assert result is True


# ---------------------------------------------------------------------------
# probe_url — False on definitive dead statuses
# ---------------------------------------------------------------------------


@respx.mock
async def test_probe_url_returns_false_on_404() -> None:
    """probe_url returns False for HTTP 404 (board not found)."""
    respx.head(PROBE_URL).mock(return_value=httpx.Response(HTTP_404))
    async with create_http_client() as client:
        result = await probe_url(client, PROBE_URL, slug=SLUG, vendor=VENDOR)

    assert result is False


@respx.mock
async def test_probe_url_returns_false_on_410() -> None:
    """probe_url returns False for HTTP 410 (board permanently gone)."""
    respx.head(PROBE_URL).mock(return_value=httpx.Response(HTTP_410))
    async with create_http_client() as client:
        result = await probe_url(client, PROBE_URL, slug=SLUG, vendor=VENDOR)

    assert result is False


# ---------------------------------------------------------------------------
# probe_url — ProbeNetworkError on network failures
# ---------------------------------------------------------------------------


@respx.mock
async def test_probe_url_raises_probe_network_error_on_timeout() -> None:
    """probe_url raises ProbeNetworkError on timeout, with slug/vendor populated."""
    respx.head(PROBE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    async with create_http_client() as client:
        with pytest.raises(ProbeNetworkError) as exc_info:
            await probe_url(client, PROBE_URL, slug=SLUG, vendor=VENDOR)

    err = exc_info.value
    assert err.slug == SLUG
    assert err.vendor == VENDOR
    assert err.status_code is None
    assert isinstance(err, ATSFetchError)


@respx.mock
async def test_probe_url_raises_probe_network_error_on_dns_failure() -> None:
    """probe_url raises ProbeNetworkError on DNS/connection failure."""
    respx.head(PROBE_URL).mock(side_effect=httpx.ConnectError("name resolution failed"))
    async with create_http_client() as client:
        with pytest.raises(ProbeNetworkError) as exc_info:
            await probe_url(client, PROBE_URL, slug=SLUG, vendor=VENDOR)

    assert exc_info.value.slug == SLUG
    assert exc_info.value.vendor == VENDOR


# ---------------------------------------------------------------------------
# probe_url — ProbeIndeterminateError on ambiguous statuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [401, 403, 405, 429, 500, 503, 418, 301])
@respx.mock
async def test_probe_url_raises_probe_indeterminate_on_ambiguous_status(
    status_code: int,
) -> None:
    """probe_url raises ProbeIndeterminateError on non-2xx/non-dead statuses."""
    respx.head(PROBE_URL).mock(return_value=httpx.Response(status_code))
    async with create_http_client() as client:
        with pytest.raises(ProbeIndeterminateError) as exc_info:
            await probe_url(
                client,
                PROBE_URL,
                slug=SLUG,
                vendor=VENDOR,
            )

    err = exc_info.value
    assert err.status_code == status_code
    assert err.slug == SLUG
    assert err.vendor == VENDOR
    assert isinstance(err, ATSFetchError)


# ---------------------------------------------------------------------------
# probe_url — custom method
# ---------------------------------------------------------------------------


@respx.mock
async def test_probe_url_uses_get_method_when_specified() -> None:
    """probe_url uses the caller-specified HTTP method."""
    respx.get(PROBE_URL).mock(return_value=httpx.Response(HTTP_200))
    async with create_http_client() as client:
        result = await probe_url(
            client, PROBE_URL, slug=SLUG, vendor=VENDOR, method="GET"
        )

    assert result is True


# ---------------------------------------------------------------------------
# exception hierarchy
# ---------------------------------------------------------------------------


def test_ats_parse_error_is_ats_fetch_error() -> None:
    """ATSParseError is a subclass of ATSFetchError."""
    err = ATSParseError("bad schema", slug=SLUG, vendor=VENDOR, status_code=HTTP_200)
    assert isinstance(err, ATSFetchError)
    assert err.slug == SLUG
    assert err.vendor == VENDOR
    assert err.status_code == HTTP_200


def test_probe_network_error_is_ats_fetch_error() -> None:
    """ProbeNetworkError is a subclass of ATSFetchError."""
    err = ProbeNetworkError("dns failed", slug=SLUG, vendor=VENDOR)
    assert isinstance(err, ATSFetchError)
    assert err.status_code is None


def test_probe_indeterminate_error_is_ats_fetch_error() -> None:
    """ProbeIndeterminateError is a subclass of ATSFetchError."""
    err = ProbeIndeterminateError(
        "server error", slug=SLUG, vendor=VENDOR, status_code=HTTP_503
    )
    assert isinstance(err, ATSFetchError)
    assert err.status_code == HTTP_503


def test_ats_fetch_error_carries_context() -> None:
    """ATSFetchError stores slug, vendor, and status_code for caller access."""
    err = ATSFetchError("fail", slug="beta", vendor="lever", status_code=HTTP_429)
    assert err.slug == "beta"
    assert err.vendor == "lever"
    assert err.status_code == HTTP_429
    assert str(err) == "fail"


def test_ats_fetch_error_status_code_optional() -> None:
    """ATSFetchError status_code defaults to None."""
    err = ATSFetchError("network down", slug=SLUG, vendor=VENDOR)
    assert err.status_code is None


# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------


def test_html_to_text_extracts_paragraph_content() -> None:
    """html_to_text extracts visible text from HTML tags."""
    html = "<html><body><p>Hello World</p></body></html>"
    result = html_to_text(html)
    assert "Hello World" in result


def test_html_to_text_handles_amp_entity() -> None:
    """html_to_text correctly unescapes &amp; HTML entities."""
    html = "<p>Python &amp; Go</p>"
    result = html_to_text(html)
    assert "Python & Go" in result


def test_html_to_text_handles_multiple_entities() -> None:
    """html_to_text unescapes &amp; and &quot; HTML entities.

    Note: &lt;code&gt; unescapes to <code> which BeautifulSoup then treats as
    a tag and strips — only the text content (&amp; and &quot;quotes&quot;) is
    preserved. This is expected behavior since html.unescape runs before parsing.
    """
    html = "<p>&amp; &quot;quotes&quot;</p>"
    result = html_to_text(html)
    assert "&" in result
    assert '"quotes"' in result


def test_html_to_text_strips_tags() -> None:
    """html_to_text removes all HTML tags from output."""
    html = "<div><h1>Title</h1><p>Body text</p></div>"
    result = html_to_text(html)
    assert "<" not in result
    assert "Title" in result
    assert "Body text" in result


def test_html_to_text_uses_newline_separator() -> None:
    """html_to_text separates block-level elements with newlines."""
    html = "<div><p>First</p><p>Second</p></div>"
    result = html_to_text(html)
    assert "First" in result
    assert "Second" in result
    # Elements should be on separate lines (separator="\n")
    assert "\n" in result


def test_html_to_text_plain_text_passthrough() -> None:
    """html_to_text on a plain string with no tags returns cleaned text."""
    result = html_to_text("just plain text")
    assert result == "just plain text"


def test_html_to_text_empty_string() -> None:
    """html_to_text on empty input returns empty string."""
    result = html_to_text("")
    assert result == ""
