"""Official detail requests for observed Greenhouse form-only embeds."""

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from jobfeed.adapters.sources._application_route_html import (
    RouteDocument,
    parse_route_document,
)
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize


def greenhouse_form_api(url: str, html: str, job: JobPosting) -> str | None:
    """Use an observed embed's board/ID only after its employer/title agree.

    Args:
        url: Actually observed Greenhouse iframe URL.
        html: Fetched form document containing its original page title.
        job: Source posting to corroborate.

    Returns:
        Official board API detail URL, or None for uncorroborated/other pages.
    """
    parsed = urlsplit(url)
    if parsed.hostname not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return None
    if parsed.path.rstrip("/") != "/embed/job_app":
        return None
    query = parse_qs(parsed.query)
    board, native_id = query.get("for", []), query.get("token", [])
    if (
        len(board) != 1
        or not re.fullmatch(r"[A-Za-z0-9_-]+", board[0])
        or len(native_id) != 1
        or not native_id[0].isdecimal()
    ):
        return None
    title = BeautifulSoup(html, "html.parser").title
    expected = f"Job Application for {job.title} at {job.company}"
    if title is None or normalize(title.get_text()) != normalize(expected):
        return None
    return f"https://boards-api.greenhouse.io/v1/boards/{board[0]}/jobs/{native_id[0]}"


def greenhouse_job_document(
    url: str, value: dict[str, Any], job: JobPosting
) -> RouteDocument:
    """Read official API title/JD; employer was corroborated on its form.

    Args:
        url: Observed ATS destination retained for verification.
        value: Official detail response with checked native ID.
        job: Source whose employer agrees with the fetched form page title.

    Returns:
        Structured target facts for the existing strict JD verification.
    """
    block = {
        "@type": "JobPosting",
        "title": value.get("title", ""),
        "description": value.get("content", ""),
        "identifier": {"value": value["id"]},
        "hiringOrganization": {"name": job.company},
    }
    html = '<script type="application/ld+json">' + json.dumps(block) + "</script>"
    return parse_route_document(url, html, job)
