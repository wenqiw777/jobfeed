"""Pure LinkedIn search-spec, pagination, and ordering helpers (no browser).

Split out of ``_linkedin_discover`` so the deterministic URL/spec/ordering logic
stays browser-free and independently testable, leaving the page-driving scrape
in the discovery module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import JobPosting

_PAGE_SIZE = 25
_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")
_NUMERIC_ID_RE = re.compile(r"(\d{4,})")


@dataclass(frozen=True, kw_only=True)
class LinkedInSearchSpec:
    """Normalized LinkedIn search URL and optional local budgets."""

    url: str
    max_jobs: int
    group: str | None = None
    group_max_jobs: int | None = None


def build_search_specs(config: SourcesLinkedInConfig) -> list[LinkedInSearchSpec]:
    """Normalize LinkedIn config search entries into concrete specs.

    Args:
        config: LinkedIn source configuration.

    Returns:
        Search specs with defaults and per-URL overrides applied.
    """
    specs: list[LinkedInSearchSpec] = []
    for entry in config.search_urls:
        if isinstance(entry, str):
            specs.append(LinkedInSearchSpec(url=entry, max_jobs=config.max_jobs))
        else:
            specs.append(
                LinkedInSearchSpec(
                    url=entry.url,
                    max_jobs=entry.max_jobs or config.max_jobs,
                    group=entry.group,
                    group_max_jobs=entry.group_max_jobs,
                )
            )
    return specs


def order_discovered_postings(
    postings: list[JobPosting],
    source_search_urls: dict[str, str],
) -> list[JobPosting]:
    """Return postings sorted by LinkedIn-specific intern priority.

    Args:
        postings: Discovered LinkedIn postings.
        source_search_urls: Canonical-id to search URL provenance map.

    Returns:
        Postings ordered fall-intern, intern, then remaining roles.
    """
    return sorted(postings, key=lambda p: _priority_key(p, source_search_urls))


def paginated_urls(base_url: str, max_jobs: int) -> list[str]:
    """Return paged search URLs covering up to ``max_jobs`` results.

    Args:
        base_url: Base LinkedIn search URL.
        max_jobs: Upper bound on results to page through.

    Returns:
        One URL per page, each carrying a ``start`` offset (25 results/page).
    """
    return [_with_start(base_url, start) for start in range(0, max_jobs, _PAGE_SIZE)]


def canonical_job_id(raw_id: str | None, href: str | None) -> str | None:
    """Derive a stable LinkedIn job id from a card attribute or its href.

    Args:
        raw_id: The card's ``data-occludable-job-id`` value, if present.
        href: The job link href, used as a fallback id source.

    Returns:
        The numeric job id when found, else a best-effort slug, else None.
    """
    if raw_id:
        match = _NUMERIC_ID_RE.search(raw_id)
        return match.group(1) if match else raw_id
    if href is None:
        return None
    match = _JOB_ID_RE.search(href)
    return match.group(1) if match else href.rstrip("/").rsplit("/", maxsplit=1)[-1]


def _with_start(url: str, start: int) -> str:
    if start == 0:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "start"]
    query.append(("start", str(start)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _priority_key(
    posting: JobPosting,
    source_search_urls: dict[str, str],
) -> tuple[int, str]:
    title = posting.title.lower()
    source_url = source_search_urls.get(posting.canonical_id, "").lower()
    if "intern" in title and (
        "fall" in title or "fall" in source_url or "2026" in title
    ):
        tier = 0
    elif "intern" in title:
        tier = 1
    else:
        tier = 2
    return tier, source_url


__all__ = [
    "LinkedInSearchSpec",
    "build_search_specs",
    "canonical_job_id",
    "order_discovered_postings",
    "paginated_urls",
]
