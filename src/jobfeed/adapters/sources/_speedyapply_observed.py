"""Resolve observed ATS metadata; never guess an employer board or job ID."""

import asyncio
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from jobfeed.adapters.sources import _ats_greenhouse as greenhouse


def greenhouse_target(requested: str, observed: str) -> tuple[str, str] | None:
    """Resolve a board and requisition from observed Greenhouse URLs.

    Args:
        requested: Original application URL.
        observed: URL observed in the browser.

    Returns:
        Observed board and requisition, or None if they cannot be verified.
    """
    source, page = urlparse(requested), urlparse(observed)
    if not re.fullmatch(
        r"(?:boards|job-boards)(?:\.[a-z]{2})?\.greenhouse\.io", page.hostname or ""
    ):
        return None
    query, original = parse_qs(page.query), parse_qs(source.query)
    slug = query.get("for", [""])[0]
    expected = original.get("gh_jid", original.get("token", [""]))[0]
    token = query.get("token", [expected])[0]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug) or not token.isdigit():
        return None
    if expected and token != expected:
        return None
    return slug, token


async def enrich_observed(
    client: httpx.AsyncClient,
    targets: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
) -> None:
    """Fill missing browser descriptions using observed official API targets.

    Args:
        client: HTTP client for official source requests.
        targets: Requested job-page targets.
        results: Mutable browser results indexed by target ID.
    """
    slots = asyncio.Semaphore(2)

    async def one(target: dict[str, Any]) -> None:
        row = results.get(target["id"])
        if not row or row.get("description"):
            return
        snapshot = row.get("page_snapshot") or {}
        urls = [
            snapshot.get("url", ""),
            *row.get("frames", []),
            *row.get("ats_urls", []),
        ]
        seen = set()
        for url in urls:
            pair = greenhouse_target(target["url"], url)
            if pair is None or pair in seen:
                continue
            seen.add(pair)
            try:
                async with slots:
                    job = await greenhouse.fetch_job(
                        client, *pair, discovered_at=datetime.now(UTC), timeout=20
                    )
                if job and job.canonical_id == pair[1] and job.jd_text:
                    results[target["id"]] = {
                        **row,
                        "description": job.jd_text,
                        "method": "observed-greenhouse-api",
                        "observed_job_id": job.canonical_id,
                        "observed_title": job.title,
                        "error": None,
                        "error_code": None,
                    }
                    return
            except Exception as exc:
                # Retain the browser fallback and an actionable API diagnostic.
                row["official_api_error"] = str(exc)

    await asyncio.gather(*(one(target) for target in targets))
