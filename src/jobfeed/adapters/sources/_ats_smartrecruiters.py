"""SmartRecruiters JD fetch helper for SpeedyApply routing.

SmartRecruiters posts have a structured public API at
``api.smartrecruiters.com/v1/companies/<company>/postings/<id>`` whose
``jobAd.sections`` splits the JD into named blocks. We concatenate them in
display order so quality scoring sees the full body.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from jobfeed.adapters.sources._http import fetch_json, html_to_text

_VENDOR = "smartrecruiters"
_API_URL = (
    "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
)

# jobs.smartrecruiters.com/<company>/<posting_id>-<slug>; only the leading
# numeric posting id is significant for the API.
_SMARTRECRUITERS_RE = re.compile(r"^https?://jobs\.smartrecruiters\.com/([^/]+)/(\d+)")

# Display order of the JD section blocks under jobAd.sections.
_SECTION_ORDER = (
    "companyDescription",
    "jobDescription",
    "qualifications",
    "additionalInformation",
)


async def fetch_jd(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    timeout: float,
) -> str:
    """Fetch and concatenate the JD sections for a SmartRecruiters apply URL.

    Args:
        client: Shared async HTTP client.
        apply_url: The SmartRecruiters apply URL from the SpeedyApply table.
        timeout: Per-request timeout in seconds.

    Returns:
        Plain-text JD (sections joined by blank lines), or empty string if the
        URL is unrecognized or no section carries text.

    Raises:
        ATSFetchError: On HTTP or network failures.
    """
    match = _SMARTRECRUITERS_RE.match(apply_url)
    if match is None:
        return ""
    company, posting_id = match.group(1), match.group(2)
    url = _API_URL.format(company=company, posting_id=posting_id)
    raw = await fetch_json(client, url, slug=company, vendor=_VENDOR, timeout=timeout)
    if not isinstance(raw, dict):
        return ""
    return _concat_sections(raw)


def _concat_sections(raw: dict[str, Any]) -> str:
    """Join the known JD section texts (HTML-stripped) in display order."""
    job_ad = raw.get("jobAd")
    sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
    if not isinstance(sections, dict):
        return ""
    chunks: list[str] = []
    for key in _SECTION_ORDER:
        section = sections.get(key)
        text = section.get("text") if isinstance(section, dict) else None
        if not text:
            continue
        chunks.append(html_to_text(str(text)).strip())
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


__all__ = ["fetch_jd"]
