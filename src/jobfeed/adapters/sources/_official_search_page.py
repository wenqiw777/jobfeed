"""Independently extract identity and JD from an observed official ATS page."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from jobfeed.adapters.sources._http import html_to_text
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.intermediary import trusted_ats_url
from jobfeed.domain.models import JobPosting, QualityBand


def parse_official_page(url: str, html: str) -> JobPosting | None:
    """Read facts from an official ATS JobPosting block, not search/model claims.

    Time complexity: O(n) in page length and its embedded structured job blocks.

    Args:
        url: Observed and fetched supported ATS URL.
        html: Public page response.

    Returns:
        Complete independently attributed posting, or none.
    """
    if not trusted_ats_url(url):
        return None
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except ValueError:
            continue
        for value in _job_blocks(data):
            job = _posting(url, value)
            if job:
                return job
    return None


def _job_blocks(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [job for item in value for job in _job_blocks(item)]
    if isinstance(value, dict):
        if value.get("@type") == "JobPosting":
            return [value]
        return _job_blocks(value.get("@graph", []))
    return []


def _posting(url: str, value: dict[str, Any]) -> JobPosting | None:
    organization = value.get("hiringOrganization")
    if not isinstance(organization, dict):
        return None
    title, company, description = (
        value.get("title"),
        organization.get("name"),
        value.get("description"),
    )
    if not all(
        isinstance(field, str) and field.strip()
        for field in (title, company, description)
    ):
        return None
    expiry = value.get("validThrough")
    if expiry:
        try:
            expires = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            if expires.replace(tzinfo=expires.tzinfo or UTC) <= datetime.now(UTC):
                return None
        except ValueError:
            return None
    location_text = _location(value.get("jobLocation"))
    if not location_text:
        return None
    posted = None
    if isinstance(value.get("datePosted"), str):
        try:
            posted = datetime.fromisoformat(value["datePosted"].replace("Z", "+00:00"))
            posted = posted.replace(tzinfo=posted.tzinfo or UTC)
        except ValueError:
            posted = None
    return JobPosting(
        platform="official_search",
        canonical_id=external_identity(url) or url,
        url=url,
        apply_url=url,
        title=str(title),
        company=re.sub(r"^\d+\s+", "", str(company)),
        location=location_text,
        jd_text=html_to_text(str(description)),
        jd_quality=QualityBand.FULL,
        discovered_at=datetime.now(UTC),
        posted_at=posted,
        enriched_at=datetime.now(UTC),
        enrich_source="official_search_jsonld",
    )


def _location(location: Any) -> str | None:
    if isinstance(location, list):
        if len(location) != 1:
            return None
        location = location[0]
    address = location.get("address") if isinstance(location, dict) else None
    if not isinstance(address, dict):
        return None
    parts = [address.get("addressLocality"), address.get("addressRegion")]
    location_text = ", ".join(part for part in parts if isinstance(part, str) and part)
    return location_text or None
