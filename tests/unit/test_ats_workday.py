"""Unit tests for the Workday two-step JD fetch helper.

All HTTP is mocked with respx — no real network. Covers:
- postingAvailable=true + CXS 200 → jd recovered, CSRF header forwarded
- postingAvailable=false → closed, no CXS request issued
- HTML 404 / CXS 410 → gone reason codes
- CXS 500 / timeout → transient failure (is_closed=False, jd_text="")
- fetch_jd backward-compat: still returns plain str
- _build_cxs_url unchanged for both host shapes
"""

from __future__ import annotations

import httpx
import respx

from jobfeed.adapters.sources._ats_workday import (
    _build_cxs_url,
    fetch_jd,
    fetch_jd_result,
)
from jobfeed.adapters.sources._http import create_http_client

TIMEOUT = 30.0

_APPLY_URL = (
    "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/"
    "Fort-Belvoir-VA/Data-Engineer-Intern_R-00180867"
)
_CXS_URL = (
    "https://leidos.wd5.myworkdayjobs.com/wday/cxs/leidos/external/job/"
    "Fort-Belvoir-VA/Data-Engineer-Intern_R-00180867"
)
_TOKEN = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

_HTML_OPEN = f"""
<html><body>
<script>
  var config = {{
    token: "{_TOKEN}",
    postingAvailable: true,
  }};
</script>
</body></html>
"""

_HTML_CLOSED = f"""
<html><body>
<script>
  var config = {{
    token: "{_TOKEN}",
    postingAvailable: false,
  }};
</script>
</body></html>
"""

_CXS_PAYLOAD = {"jobPostingInfo": {"jobDescription": "<p>hi</p>"}}


# ---------------------------------------------------------------------------
# fetch_jd_result — two-step flow
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_jd_result_open_posting_returns_jd() -> None:
    """postingAvailable=true + CXS 200 → non-empty jd_text, is_closed=False."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    cxs_route = respx.get(_CXS_URL).mock(
        return_value=httpx.Response(200, json=_CXS_PAYLOAD)
    )

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is False
    assert result.jd_text != ""
    assert "hi" in result.jd_text
    assert result.reason is None
    assert cxs_route.called


@respx.mock
async def test_fetch_jd_result_csrf_token_forwarded() -> None:
    """CXS request carries X-CALYPSO-CSRF-TOKEN equal to the HTML token."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    cxs_route = respx.get(_CXS_URL).mock(
        return_value=httpx.Response(200, json=_CXS_PAYLOAD)
    )

    async with create_http_client() as client:
        await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    sent_headers = dict(cxs_route.calls.last.request.headers)
    assert sent_headers.get("x-calypso-csrf-token") == _TOKEN


@respx.mock
async def test_fetch_jd_result_closed_posting_no_cxs() -> None:
    """postingAvailable=false → closed reason, jd_text='', CXS not called."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_CLOSED))
    cxs_route = respx.get(_CXS_URL).mock(
        return_value=httpx.Response(200, json=_CXS_PAYLOAD)
    )

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is True
    assert result.reason == "closed:posting-unavailable:workday"
    assert result.jd_text == ""
    assert not cxs_route.called


@respx.mock
async def test_fetch_jd_result_html_404_is_gone() -> None:
    """HTML 404 → is_closed=True, reason='gone:404:workday'."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(404))

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is True
    assert result.reason == "gone:404:workday"
    assert result.jd_text == ""


@respx.mock
async def test_fetch_jd_result_cxs_410_is_gone() -> None:
    """CXS 410 → is_closed=True, reason='gone:410:workday'."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    respx.get(_CXS_URL).mock(return_value=httpx.Response(410))

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is True
    assert result.reason == "gone:410:workday"
    assert result.jd_text == ""


@respx.mock
async def test_fetch_jd_result_cxs_500_is_transient() -> None:
    """CXS 500 with postingAvailable=true → is_closed=False, jd_text=''."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    respx.get(_CXS_URL).mock(return_value=httpx.Response(500))

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is False
    assert result.jd_text == ""
    assert result.reason is None


@respx.mock
async def test_fetch_jd_result_cxs_timeout_is_transient() -> None:
    """CXS timeout with postingAvailable=true → is_closed=False, jd_text=''."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    respx.get(_CXS_URL).mock(side_effect=httpx.ReadTimeout("timed out", request=None))

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is False
    assert result.jd_text == ""
    assert result.reason is None


@respx.mock
async def test_fetch_jd_result_unrecognized_url_is_transient() -> None:
    """An unrecognized apply URL (no CXS mapping) → transient empty."""
    async with create_http_client() as client:
        result = await fetch_jd_result(
            client, "https://example.com/jobs/123", timeout=TIMEOUT
        )

    assert result.is_closed is False
    assert result.jd_text == ""
    assert result.reason is None


@respx.mock
async def test_fetch_jd_result_follows_html_redirect() -> None:
    """A 301/302 redirect on the apply HTML is followed to the real job page.

    Proves the per-fetch client preserves ``follow_redirects=True`` from the
    shared client. Without it, the redirect would surface as a 30x and become
    a transient miss instead of recovering the JD.
    """
    redirect_url = (
        "https://leidos.wd5.myworkdayjobs.com/en-US/external/job/"
        "Fort-Belvoir-VA/Data-Engineer-Intern_R-00180867/redirected"
    )
    respx.get(_APPLY_URL).mock(
        return_value=httpx.Response(302, headers={"Location": redirect_url})
    )
    respx.get(redirect_url).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    cxs_route = respx.get(_CXS_URL).mock(
        return_value=httpx.Response(200, json=_CXS_PAYLOAD)
    )

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is False
    assert "hi" in result.jd_text
    assert result.reason is None
    assert cxs_route.called


# ---------------------------------------------------------------------------
# fetch_jd backward-compat
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_jd_returns_plain_str() -> None:
    """fetch_jd still returns a plain str (backward-compat for callers)."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_OPEN))
    respx.get(_CXS_URL).mock(return_value=httpx.Response(200, json=_CXS_PAYLOAD))

    async with create_http_client() as client:
        result = await fetch_jd(client, _APPLY_URL, timeout=TIMEOUT)

    assert isinstance(result, str)
    assert "hi" in result


@respx.mock
async def test_fetch_jd_closed_returns_empty_str() -> None:
    """fetch_jd returns '' when the posting is closed."""
    respx.get(_APPLY_URL).mock(return_value=httpx.Response(200, text=_HTML_CLOSED))

    async with create_http_client() as client:
        result = await fetch_jd(client, _APPLY_URL, timeout=TIMEOUT)

    assert result == ""


# ---------------------------------------------------------------------------
# _build_cxs_url unchanged — both host shapes
# ---------------------------------------------------------------------------


def test_build_cxs_url_myworkdayjobs_with_locale() -> None:
    """myworkdayjobs.com with locale segment maps correctly."""
    result = _build_cxs_url(
        "https://acme.wd5.myworkdayjobs.com/en-US/CareerSite/job/REQ-1"
    )
    assert result == (
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/CareerSite/job/REQ-1",
        "acme",
    )


def test_build_cxs_url_myworkdayjobs_no_locale() -> None:
    """myworkdayjobs.com without locale segment maps correctly."""
    result = _build_cxs_url("https://acme.wd5.myworkdayjobs.com/CareerSite/job/REQ-1")
    assert result == (
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/CareerSite/job/REQ-1",
        "acme",
    )


def test_build_cxs_url_myworkdaysite() -> None:
    """myworkdaysite.com shape maps correctly."""
    result = _build_cxs_url(
        "https://wd5.myworkdaysite.com/recruiting/acme/CareerSite/job/REQ-1"
    )
    assert result == (
        "https://wd5.myworkdaysite.com/wday/cxs/acme/CareerSite/job/REQ-1",
        "acme",
    )


def test_build_cxs_url_unrecognized_returns_none() -> None:
    """Unrecognized URLs return None."""
    assert _build_cxs_url("https://example.com/jobs/123") is None


# ---------------------------------------------------------------------------
# Cookie isolation
# ---------------------------------------------------------------------------

_APPLY_URL_2 = "https://other.wd5.myworkdayjobs.com/en-US/jobs/job/Some-Role_R-00000001"
_CXS_URL_2 = (
    "https://other.wd5.myworkdayjobs.com/wday/cxs/other/jobs/job/Some-Role_R-00000001"
)
_TOKEN_2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_HTML_OPEN_2 = f"""
<html><body>
<script>
  var config = {{
    token: "{_TOKEN_2}",
    postingAvailable: true,
  }};
</script>
</body></html>
"""

_SET_COOKIE_HEADER = "wd_browser_id=abc123; Domain=.myworkdayjobs.com; Path=/"


@respx.mock
async def test_cross_fetch_cookie_isolation() -> None:
    """Cookie set by fetch #1's HTML response must NOT appear in fetch #2's requests.

    The shared ``httpx.AsyncClient`` jar must NOT accumulate cookies between
    independent ``fetch_jd_result`` calls.
    """
    # fetch #1 — HTML sets a cookie
    respx.get(_APPLY_URL).mock(
        return_value=httpx.Response(
            200,
            text=_HTML_OPEN,
            headers={"Set-Cookie": _SET_COOKIE_HEADER},
        )
    )
    respx.get(_CXS_URL).mock(return_value=httpx.Response(200, json=_CXS_PAYLOAD))

    # fetch #2 — capture outgoing Cookie headers for both requests
    html2_route = respx.get(_APPLY_URL_2).mock(
        return_value=httpx.Response(200, text=_HTML_OPEN_2)
    )
    cxs2_route = respx.get(_CXS_URL_2).mock(
        return_value=httpx.Response(200, json=_CXS_PAYLOAD)
    )

    async with create_http_client() as client:
        await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)
        await fetch_jd_result(client, _APPLY_URL_2, timeout=TIMEOUT)

    html2_cookie = html2_route.calls.last.request.headers.get("cookie", "")
    cxs2_cookie = cxs2_route.calls.last.request.headers.get("cookie", "")

    # The cookie planted by fetch #1 must not bleed into fetch #2
    assert "wd_browser_id" not in html2_cookie, (
        f"fetch #1 cookie leaked onto fetch #2 HTML request: {html2_cookie!r}"
    )
    assert "wd_browser_id" not in cxs2_cookie, (
        f"fetch #1 cookie leaked onto fetch #2 CXS request: {cxs2_cookie!r}"
    )


@respx.mock
async def test_intra_fetch_cookie_sharing() -> None:
    """Cookie set by the HTML step IS present on the CXS request within one fetch.

    The CXS GET must carry cookies established by the HTML GET so the
    session-scoped token is transmitted correctly.
    """
    html_route = respx.get(_APPLY_URL).mock(
        return_value=httpx.Response(
            200,
            text=_HTML_OPEN,
            headers={"Set-Cookie": _SET_COOKIE_HEADER},
        )
    )
    cxs_route = respx.get(_CXS_URL).mock(
        return_value=httpx.Response(200, json=_CXS_PAYLOAD)
    )

    async with create_http_client() as client:
        result = await fetch_jd_result(client, _APPLY_URL, timeout=TIMEOUT)

    assert result.is_closed is False
    assert html_route.called
    assert cxs_route.called

    cxs_cookie = cxs_route.calls.last.request.headers.get("cookie", "")
    assert "wd_browser_id" in cxs_cookie, (
        f"cookie from HTML step not forwarded to CXS step: {cxs_cookie!r}"
    )
