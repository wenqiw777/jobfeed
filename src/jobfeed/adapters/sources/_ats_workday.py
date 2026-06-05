"""Workday JD fetch helper for SpeedyApply routing.

Workday apply URLs come in two host shapes that both map to the same public
``wday/cxs`` JSON endpoint:

  * ``<tenant>.<region>.myworkdayjobs.com/<lang>/<board>/job/<rest>``
  * ``<region>.myworkdaysite.com/recruiting/<tenant>/<board>/job/<rest>``

Both transform to ``https://<host>/wday/cxs/<tenant>/<board>/job/<rest>``, whose
``jobPostingInfo.jobDescription`` carries the HTML JD body.

Two-step fetch protocol (empirically verified):
  1. GET the apply HTML URL — seeds session cookies; HTML embeds ``postingAvailable``
     and ``token`` (CSRF) in an inline JS config object.
  2. If ``postingAvailable: false`` → job is closed; skip CXS.
     If ``postingAvailable: true`` → GET the CXS API with the CSRF token header
     on the *same* request (cookies already set by step 1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from jobfeed.adapters.sources._http import html_to_text

_VENDOR = "workday"
_DEAD_STATUSES = frozenset({404, 410})

# <tenant>.<region>.myworkdayjobs.com/[<lang>/]<board>/job/<rest>; tenant is the
# leading subdomain label. The locale segment is OPTIONAL — many Workday sites
# omit it (e.g. .../<board>/job/<rest>), so <board> is matched as the segment
# immediately before /job/ whether or not a <lang> precedes it.
_WORKDAY_JOBS_RE = re.compile(
    r"^https?://(?P<host>[^/]+\.myworkdayjobs\.com)/"
    r"(?:(?P<lang>[^/]+)/)?(?P<board>[^/]+)/job/(?P<rest>.+?)(?:\?.*)?$"
)
# <region>.myworkdaysite.com/recruiting/<tenant>/<board>/job/<rest>; tenant lives
# in the path here, not the subdomain.
_WORKDAY_SITE_RE = re.compile(
    r"^https?://(?P<host>[^/]+\.myworkdaysite\.com)/"
    r"recruiting/(?P<tenant>[^/]+)/(?P<board>[^/]+)/job/(?P<rest>.+?)(?:\?.*)?$"
)

# Inline JS patterns in the Workday apply page HTML
_RE_POSTING_AVAILABLE = re.compile(r"postingAvailable:\s*(true|false)")
_RE_CSRF_TOKEN = re.compile(r'\btoken:\s*"([0-9a-f-]{36})"')


@dataclass(frozen=True)
class WorkdayFetch:
    """Result of a Workday two-step JD fetch.

    Attributes:
        jd_text: Plain-text JD body, or empty string if unavailable.
        is_closed: True when the posting is definitively closed/gone.
        reason: Machine-readable close reason (e.g.
            ``closed:posting-unavailable:workday``),
            or None when the result is either a live JD or a transient failure.
    """

    jd_text: str
    is_closed: bool
    reason: str | None


def _build_cxs_url(apply_url: str) -> tuple[str, str] | None:
    """Return (cxs_url, slug) for a Workday apply URL, or None if unrecognized.

    ``slug`` is the tenant, used only for HTTP error context.
    """
    match = _WORKDAY_JOBS_RE.match(apply_url)
    if match:
        host = match.group("host")
        tenant = host.split(".")[0]
        board = match.group("board")
        rest = match.group("rest")
        return (f"https://{host}/wday/cxs/{tenant}/{board}/job/{rest}", tenant)
    match = _WORKDAY_SITE_RE.match(apply_url)
    if match:
        host = match.group("host")
        tenant = match.group("tenant")
        board = match.group("board")
        rest = match.group("rest")
        return (f"https://{host}/wday/cxs/{tenant}/{board}/job/{rest}", tenant)
    return None


def _parse_posting_flag(html_body: str) -> bool | None:
    """Extract postingAvailable boolean from inline JS, or None if absent."""
    m = _RE_POSTING_AVAILABLE.search(html_body)
    if m is None:
        return None
    return m.group(1) == "true"


def _parse_csrf_token(html_body: str) -> str | None:
    """Extract the 36-char CSRF token from inline JS, or None if absent."""
    m = _RE_CSRF_TOKEN.search(html_body)
    return m.group(1) if m else None


def _extract_jd_text(raw: Any) -> str:
    """Pull jobDescription text from CXS JSON payload."""
    if not isinstance(raw, dict):
        return ""
    info = raw.get("jobPostingInfo")
    if not isinstance(info, dict):
        return ""
    description = str(info.get("jobDescription") or "").strip()
    if not description:
        return ""
    return html_to_text(description).strip()


async def _fetch_html(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    timeout: float,
) -> tuple[int, str]:
    """GET the apply HTML; return (status_code, body_text)."""
    try:
        response = await client.get(apply_url, timeout=timeout)
        return response.status_code, response.text
    except (httpx.TimeoutException, httpx.TransportError):
        return 0, ""


async def _fetch_cxs(
    client: httpx.AsyncClient,
    cxs_url: str,
    *,
    token: str,
    apply_url: str,
    timeout: float,
) -> tuple[int, Any]:
    """GET the CXS API with CSRF headers; return (status_code, json_or_none)."""
    headers = {
        "X-CALYPSO-CSRF-TOKEN": token,
        "Accept": "application/json",
        "Referer": apply_url,
    }
    try:
        response = await client.get(cxs_url, headers=headers, timeout=timeout)
        status = response.status_code
        if response.is_success:
            try:
                return status, response.json()
            except Exception:
                return status, None
        return status, None
    except (httpx.TimeoutException, httpx.TransportError):
        return 0, None


async def fetch_jd_result(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    timeout: float,
) -> WorkdayFetch:
    """Fetch the JD for a Workday apply URL using the two-step protocol.

    Step 1: GET the apply HTML page (seeds per-fetch cookies + yields
            postingAvailable + token).
    Step 2: If open, GET the CXS endpoint with the CSRF token.

    A short-lived ``httpx.AsyncClient`` is created per call, sharing only the
    transport (connection pool) of ``client`` but owning a fresh cookie jar.
    This prevents cookies set by one tenant's HTML response from leaking onto
    subsequent fetches that reuse the same long-lived ``client``.  Within a
    single fetch the HTML and CXS requests share the same isolated jar, so
    session cookies flow from step 1 to step 2 as required.

    Args:
        client: Shared async HTTP client (provides connection pool / transport).
        apply_url: The Workday apply URL from the SpeedyApply table.
        timeout: Per-request timeout in seconds.

    Returns:
        WorkdayFetch with jd_text, is_closed, and reason populated.
    """
    built = _build_cxs_url(apply_url)
    if built is None:
        return WorkdayFetch("", False, None)

    cxs_url, _slug = built

    async with httpx.AsyncClient(transport=client._transport) as fetch_client:
        html_status, html_body = await _fetch_html(
            fetch_client, apply_url, timeout=timeout
        )

        if html_status in _DEAD_STATUSES:
            return WorkdayFetch("", True, f"gone:{html_status}:{_VENDOR}")
        if html_status == 0 or not html_body:
            return WorkdayFetch("", False, None)

        is_available = _parse_posting_flag(html_body)
        if is_available is False:
            return WorkdayFetch("", True, f"closed:posting-unavailable:{_VENDOR}")

        token = _parse_csrf_token(html_body)
        if token is None:
            return WorkdayFetch("", False, None)

        cxs_status, cxs_json = await _fetch_cxs(
            fetch_client, cxs_url, token=token, apply_url=apply_url, timeout=timeout
        )

        if cxs_status in _DEAD_STATUSES:
            return WorkdayFetch("", True, f"gone:{cxs_status}:{_VENDOR}")
        if cxs_status == 0 or cxs_json is None:
            return WorkdayFetch("", False, None)

        jd_text = _extract_jd_text(cxs_json)
        return WorkdayFetch(jd_text, False, None)


async def fetch_jd(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    timeout: float,
) -> str:
    """Fetch the JD body for a Workday apply URL as plain text.

    Delegates to ``fetch_jd_result`` and returns ``.jd_text`` for backward
    compatibility with existing routing callers.

    Args:
        client: Shared async HTTP client.
        apply_url: The Workday apply URL from the SpeedyApply table.
        timeout: Per-request timeout in seconds.

    Returns:
        Plain-text JD, or empty string if the URL is unrecognized, closed,
        or the response lacks a job description.
    """
    result = await fetch_jd_result(client, apply_url, timeout=timeout)
    return result.jd_text


__all__ = ["WorkdayFetch", "_build_cxs_url", "fetch_jd", "fetch_jd_result"]
