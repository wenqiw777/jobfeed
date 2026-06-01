"""LinkedIn Playwright discovery helpers."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality, quality_rank
from jobfeed.ports.source import DiscoverResult

from ._linkedin_dom import (
    CARD_SELECTOR,
    COMPANY_SELECTORS,
    JOB_LINK_SELECTOR,
    LOCATION_SELECTORS,
    looks_like_authwall,
    read_body_text,
    read_first_attr,
    read_first_text,
    read_job_description,
)

Sleeper = Callable[[float], Awaitable[None]]
_PAGE_SIZE = 25
_GOOD_RANK = quality_rank(QualityBand.GOOD)
_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")
_NUMERIC_ID_RE = re.compile(r"(\d{4,})")


@dataclass(frozen=True, kw_only=True)
class LinkedInSearchSpec:
    """Normalized LinkedIn search URL and optional local budgets."""

    url: str
    max_jobs: int
    group: str | None = None
    group_max_jobs: int | None = None


@dataclass(kw_only=True)
class _DiscoverState:
    postings: list[JobPosting] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    group_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    source_search_urls: dict[str, str]


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


async def discover_linkedin_jobs(
    page: Any,
    config: SourcesLinkedInConfig,
    source_search_urls: dict[str, str],
    sleeper: Sleeper,
    logger: Any,
) -> DiscoverResult:
    """Discover LinkedIn job cards using the active Playwright page.
    Args:
        page: Playwright page opened on the configured LinkedIn profile.
        config: LinkedIn source configuration.
        source_search_urls: Mutable canonical-id to search URL provenance map.
        sleeper: Async pacing hook.
        logger: Structured logger.
    Returns:
        Discovery result with ordered postings or a reauth signal.
    """
    started = monotonic()
    state = _DiscoverState(source_search_urls=source_search_urls)
    for spec in build_search_specs(config):
        if await _discover_spec(page, spec, state, sleeper, logger):
            return _reauth_result(state.postings, started)
    ordered = order_discovered_postings(state.postings, source_search_urls)
    return DiscoverResult(postings=ordered, duration_s=monotonic() - started)


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


async def _discover_spec(
    page: Any,
    spec: LinkedInSearchSpec,
    state: _DiscoverState,
    sleeper: Sleeper,
    logger: Any,
) -> bool:
    """Scrape one search spec; return True when the page demands reauth."""
    accepted = 0
    for search_url in _paginated_urls(spec.url, spec.max_jobs):
        if not _can_accept(spec, accepted, state.group_counts):
            return False
        await page.goto(search_url, wait_until="domcontentloaded")
        await sleeper(0)
        await _scroll_results(page)
        body_text = await read_body_text(page)
        if looks_like_authwall(getattr(page, "url", search_url), body_text):
            logger.error("linkedin_discover_reauth_required", url=search_url)
            return True
        new_jobs = await _read_cards(page, search_url)
        if not new_jobs:
            return False
        accepted = _accept_jobs(
            spec,
            new_jobs,
            state,
            search_url,
            accepted,
        )
    return False


async def _read_cards(page: Any, search_url: str) -> list[JobPosting]:
    cards = page.locator(CARD_SELECTOR)
    count = await cards.count()
    jobs: list[JobPosting] = []
    for index in range(count):
        posting = await _posting_from_card(page, cards.nth(index), search_url)
        if posting is not None:
            jobs.append(posting)
    return jobs


async def _posting_from_card(
    page: Any,
    card: Any,
    search_url: str,
) -> JobPosting | None:
    href = await read_first_attr(card, (JOB_LINK_SELECTOR,), "href")
    raw_id = await _read_card_job_id(card)
    canonical_id = _canonical_job_id(raw_id, href)
    if canonical_id is None or href is None:
        return None
    title = await read_first_text(card, (JOB_LINK_SELECTOR,))
    company = await read_first_text(card, COMPANY_SELECTORS)
    location = await read_first_text(card, LOCATION_SELECTORS)
    await card.click()
    jd_text = await read_job_description(page)
    quality = assess_quality(jd_text)
    now = datetime.now(UTC)
    return JobPosting(
        platform="linkedin",
        canonical_id=canonical_id,
        url=urljoin(search_url, href),
        title=title or "LinkedIn job",
        company=company or "Unknown company",
        location=location or "Unknown",
        discovered_at=now,
        jd_text=jd_text or None,
        jd_quality=quality,
        enriched_at=now if quality_rank(quality) >= _GOOD_RANK else None,
        enrich_source="linkedin_inline" if jd_text else None,
    )


def _accept_jobs(
    spec: LinkedInSearchSpec,
    new_jobs: list[JobPosting],
    state: _DiscoverState,
    search_url: str,
    accepted: int,
) -> int:
    for posting in new_jobs:
        if posting.canonical_id in state.seen:
            continue
        if not _can_accept(spec, accepted, state.group_counts):
            break
        state.postings.append(posting)
        state.seen.add(posting.canonical_id)
        state.source_search_urls[posting.canonical_id] = search_url
        accepted += 1
        if spec.group is not None:
            state.group_counts[spec.group] += 1
    return accepted


def _can_accept(
    spec: LinkedInSearchSpec,
    accepted: int,
    group_counts: dict[str, int],
) -> bool:
    if accepted >= spec.max_jobs:
        return False
    if spec.group is None or spec.group_max_jobs is None:
        return True
    return group_counts[spec.group] < spec.group_max_jobs


async def _read_card_job_id(card: Any) -> str | None:
    try:
        value = await card.get_attribute("data-occludable-job-id")
    except Exception:
        return None
    return value if isinstance(value, str) else None


async def _scroll_results(page: Any) -> None:
    try:
        await page.mouse.wheel(0, 2500)
    except Exception:
        return


def _canonical_job_id(raw_id: str | None, href: str | None) -> str | None:
    if raw_id:
        match = _NUMERIC_ID_RE.search(raw_id)
        return match.group(1) if match else raw_id
    if href is None:
        return None
    match = _JOB_ID_RE.search(href)
    return match.group(1) if match else href.rstrip("/").rsplit("/", maxsplit=1)[-1]


def _paginated_urls(base_url: str, max_jobs: int) -> list[str]:
    return [_with_start(base_url, start) for start in range(0, max_jobs, _PAGE_SIZE)]


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


def _reauth_result(postings: list[JobPosting], started: float) -> DiscoverResult:
    return DiscoverResult(
        postings=postings,
        needs_reauth=True,
        error="LinkedIn login required",
        duration_s=monotonic() - started,
    )


__all__ = [
    "LinkedInSearchSpec",
    "build_search_specs",
    "discover_linkedin_jobs",
    "order_discovered_postings",
]
