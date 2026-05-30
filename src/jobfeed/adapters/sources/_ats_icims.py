"""iCIMS JD fetch helper for SpeedyApply routing.

iCIMS apply pages wrap the real JD in an iframe; appending ``?in_iframe=1``
returns an HTML page carrying a JSON-LD ``<script type="application/ld+json">``
``JobPosting`` whose ``description`` is the HTML JD body. Unlike the JSON-API
vendors, this fetch returns raw HTML (via ``_http.fetch_text``), then we extract
the JSON-LD block.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from jobfeed.adapters.sources._http import fetch_text, html_to_text

_VENDOR = "icims"

# careers-<tenant>.icims.com/jobs/<id>/<slug>/job; slug captures host + the
# numeric-id-bearing path so we can rebuild the iframe URL.
_ICIMS_RE = re.compile(r"^https?://([a-z0-9-]+\.icims\.com)/(jobs/(\d+)[^?]*)")

# JSON-LD blob inside <script type="application/ld+json"> ... </script>.
_JSONLD_RE = re.compile(
    r'application/ld\+json"?[^>]*>\s*(\{.+?\})\s*</script>',
    re.DOTALL,
)


def _iframe_url(apply_url: str) -> tuple[str, str] | None:
    """Return (iframe_url, slug) for an iCIMS apply URL, or None if unrecognized.

    ``slug`` is the iCIMS host, used only for HTTP error context. ``in_iframe=1``
    is appended with the correct separator depending on an existing query.
    """
    match = _ICIMS_RE.match(apply_url)
    if match is None:
        return None
    host, path = match.group(1), match.group(2)
    separator = "&" if "?" in path else "?"
    return (f"https://{host}/{path}{separator}in_iframe=1", host)


async def fetch_jd(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    timeout: float,
) -> str:
    """Fetch the iCIMS iframe page and return its JSON-LD JD as plain text.

    Args:
        client: Shared async HTTP client.
        apply_url: The iCIMS apply URL from the SpeedyApply table.
        timeout: Per-request timeout in seconds.

    Returns:
        Plain-text JD, or empty string if the URL is unrecognized or the page
        carries no JSON-LD JobPosting description.

    Raises:
        ATSFetchError: On HTTP or network failures.
    """
    built = _iframe_url(apply_url)
    if built is None:
        return ""
    iframe_url, slug = built
    html_text = await fetch_text(
        client, iframe_url, slug=slug, vendor=_VENDOR, timeout=timeout
    )
    return _extract_jsonld_jd(html_text)


def _extract_jsonld_jd(html_text: str) -> str:
    """Find the JSON-LD JobPosting and return its description as plain text.

    Returns '' when no JobPosting block exists (the iframe occasionally returns
    a marketing landing instead of the job, e.g. when the id has rolled off).
    """
    for match in _JSONLD_RE.finditer(html_text):
        description = _description_from_blob(match.group(1))
        if description:
            return html_to_text(description).strip()
    return ""


def _description_from_blob(blob: str) -> str:
    """Parse one JSON-LD blob and return the first JobPosting description."""
    try:
        data = json.loads(blob)
    except ValueError:
        return ""
    for candidate in _job_posting_candidates(data):
        description = candidate.get("description")
        if description:
            return str(description)
    return ""


def _job_posting_candidates(data: Any) -> list[dict[str, Any]]:
    """Collect JobPosting dicts from a JSON-LD payload (direct or @graph)."""
    if not isinstance(data, dict):
        return []
    candidates: list[dict[str, Any]] = []
    if data.get("@type") == "JobPosting":
        candidates.append(data)
    for item in data.get("@graph") or []:
        if isinstance(item, dict) and item.get("@type") == "JobPosting":
            candidates.append(item)
    return candidates


__all__ = ["fetch_jd"]
