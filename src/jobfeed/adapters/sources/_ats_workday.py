"""Workday JD fetch helper for SpeedyApply routing.

Workday apply URLs come in two host shapes that both map to the same public
``wday/cxs`` JSON endpoint:

  * ``<tenant>.<region>.myworkdayjobs.com/<lang>/<board>/job/<rest>``
  * ``<region>.myworkdaysite.com/recruiting/<tenant>/<board>/job/<rest>``

Both transform to ``https://<host>/wday/cxs/<tenant>/<board>/job/<rest>``, whose
``jobPostingInfo.jobDescription`` carries the HTML JD body.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from jobfeed.adapters.sources._http import fetch_json, html_to_text

_VENDOR = "workday"

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


async def fetch_jd(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    timeout: float,
) -> str:
    """Fetch the JD body for a Workday apply URL as plain text.

    Args:
        client: Shared async HTTP client.
        apply_url: The Workday apply URL from the SpeedyApply table.
        timeout: Per-request timeout in seconds.

    Returns:
        Plain-text JD, or empty string if the URL is unrecognized or the
        response lacks a job description.

    Raises:
        ATSFetchError: On HTTP or network failures.
    """
    built = _build_cxs_url(apply_url)
    if built is None:
        return ""
    cxs_url, slug = built
    raw = await fetch_json(client, cxs_url, slug=slug, vendor=_VENDOR, timeout=timeout)
    if not isinstance(raw, dict):
        return ""
    info = raw.get("jobPostingInfo")
    if not isinstance(info, dict):
        return ""
    description = _str_or_empty(info.get("jobDescription"))
    if not description:
        return ""
    return html_to_text(description).strip()


def _str_or_empty(value: Any) -> str:
    """Coerce a possibly-None value to a stripped string."""
    return str(value or "").strip()


__all__ = ["fetch_jd"]
