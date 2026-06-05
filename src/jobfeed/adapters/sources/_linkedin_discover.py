"""LinkedIn Playwright discovery: drive the page and harvest job cards.

Pure URL/spec/ordering logic lives in ``_linkedin_search``; this module owns the
page-driving scrape. ``build_search_specs``/``order_discovered_postings``/
``LinkedInSearchSpec`` are re-exported for callers that import them from here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from urllib.parse import urljoin

from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality, quality_rank
from jobfeed.ports.source import DiscoverResult

from ._linkedin_dom import (
    CARD_SELECTOR,
    COMPANY_SELECTORS,
    JOB_LINK_SELECTOR,
    LOCATION_SELECTORS,
    human_delay,
    looks_like_authwall,
    read_body_text,
    read_first_attr,
    read_first_text,
    read_job_description,
)
from ._linkedin_search import (
    LinkedInSearchSpec,
    build_search_specs,
    canonical_job_id,
    order_discovered_postings,
    paginated_urls,
)

Sleeper = Callable[[float], Awaitable[None]]
_GOOD_RANK = quality_rank(QualityBand.GOOD)
# Bounded paced scroll passes: LinkedIn lazy-loads job cards, so a single nudge
# under-collects the tail of each page.
_SCROLL_PASSES = 4


@dataclass(kw_only=True)
class _DiscoverState:
    postings: list[JobPosting] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    group_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    source_search_urls: dict[str, str]


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


async def _discover_spec(
    page: Any,
    spec: LinkedInSearchSpec,
    state: _DiscoverState,
    sleeper: Sleeper,
    logger: Any,
) -> bool:
    # Returns True when the page hits an authwall and the caller must reauth.
    accepted = 0
    for search_url in paginated_urls(spec.url, spec.max_jobs):
        if not _can_accept(spec, accepted, state.group_counts):
            return False
        await page.goto(search_url, wait_until="domcontentloaded")
        await sleeper(human_delay())
        await _scroll_results(page, sleeper)
        body_text = await read_body_text(page)
        if looks_like_authwall(getattr(page, "url", search_url), body_text):
            logger.error("linkedin_discover_reauth_required", url=search_url)
            return True
        new_jobs = await _read_cards(page, search_url)
        if not new_jobs:
            return False
        accepted = _accept_jobs(spec, new_jobs, state, search_url, accepted)
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
    cid = canonical_job_id(raw_id, href)
    if cid is None or href is None:
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
        canonical_id=cid,
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


async def _scroll_results(page: Any, sleeper: Sleeper) -> None:
    # A few paced passes so lazy-loaded cards render; O(_SCROLL_PASSES). A dead
    # browser surfaces via the subsequent description/attribute reads, not here.
    for _pass in range(_SCROLL_PASSES):
        try:
            await page.mouse.wheel(0, 2500)
        except Exception:
            return
        await sleeper(human_delay())


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
