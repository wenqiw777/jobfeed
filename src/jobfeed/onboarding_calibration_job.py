"""Select a representative real Indeed JD for onboarding calibration."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jobfeed.adapters.sources import _jobspy_process
from jobfeed.adapters.sources._jobspy_patches import apply_indeed_date_patch
from jobfeed.config import SourcesIndeedConfig
from jobfeed.domain.models import JobPosting
from jobfeed.observability import JobfeedLogger
from jobfeed.onboarding_searches import SearchDraftState

_REPRESENTATIVE_POOL_LIMIT = 30
_MIN_COMPLETE_JD_CHARS = 100
_GENERIC_QUERY_TERMS = frozenset(
    {"intern", "internship", "new", "graduate", "entry", "level", "junior"}
)


class _IndeedFetcher(Protocol):
    async def __call__(
        self, search_urls: list[str], limit: int
    ) -> list[JobPosting]: ...


@dataclass(frozen=True, slots=True)
class CalibrationJobSample:
    """One unchanged Indeed posting chosen from the confirmed searches."""

    id: str
    title: str
    company: str
    url: str
    jd_text: str


class OnboardingCalibrationJobSampler:
    """Choose the real JD closest to a 30-posting sample's mean length."""

    def __init__(
        self,
        *,
        search_state: Callable[[], SearchDraftState],
        fetch_indeed: _IndeedFetcher,
    ) -> None:
        self._search_state = search_state
        self._fetch_indeed = fetch_indeed

    async def sample(self) -> CalibrationJobSample | None:
        """Return a representative confirmed-search JD without fabrication.

        Returns:
            Real posting nearest the sample's mean JD length, or None.
        """
        enabled_searches = [
            search
            for search in self._search_state().searches
            if search.enabled and search.source == "indeed"
        ]
        search_urls = [search.url for search in enabled_searches]
        if not search_urls:
            return None
        postings = await self._fetch_indeed(
            search_urls,
            _REPRESENTATIVE_POOL_LIMIT,
        )
        complete = _complete_unique_postings(postings)
        pool = _relevant_postings(
            complete,
            [search.query for search in enabled_searches],
        )[:_REPRESENTATIVE_POOL_LIMIT]
        if not pool:
            return None
        mean_length = sum(_jd_length(posting) for posting in pool) / len(pool)
        posting = min(
            pool,
            key=lambda candidate: abs(_jd_length(candidate) - mean_length),
        )
        return CalibrationJobSample(
            id=posting.id or posting.canonical_id,
            title=posting.title,
            company=posting.company,
            url=posting.url,
            jd_text=(posting.jd_text or "").strip(),
        )


async def fetch_indeed_sample(
    search_urls: list[str],
    limit: int,
    *,
    config: SourcesIndeedConfig,
    logger: JobfeedLogger,
) -> list[JobPosting]:
    """Fetch a bounded onboarding sample through the existing Indeed adapter.

    Args:
        search_urls: Confirmed Indeed searches to sample.
        limit: Approximate total posting limit across searches.
        config: Effective Indeed adapter settings.
        logger: Structured application logger.

    Returns:
        Postings returned by the existing Indeed scraping adapter.
    """
    if not search_urls:
        return []
    apply_indeed_date_patch()
    per_url_limit = max(1, math.ceil(limit / len(search_urls)))
    return await _jobspy_process.scrape_urls(
        site_name="indeed",
        platform="indeed",
        search_urls=search_urls,
        max_jobs=per_url_limit,
        hours_old=None,
        timeout_s=config.timeout_s,
        max_concurrent=config.max_concurrent,
        logger=logger,
        discovered_at=datetime.now(UTC),
        country_indeed=config.country_indeed,
        repeat=1,
    )


def _complete_unique_postings(postings: list[JobPosting]) -> list[JobPosting]:
    seen: set[str] = set()
    complete: list[JobPosting] = []
    for posting in postings:
        jd_text = (posting.jd_text or "").strip()
        if len(jd_text) < _MIN_COMPLETE_JD_CHARS:
            continue
        key = posting.canonical_id
        if key in seen:
            continue
        seen.add(key)
        complete.append(posting)
    return complete


def _jd_length(posting: JobPosting) -> int:
    return len((posting.jd_text or "").strip())


def _relevant_postings(
    postings: list[JobPosting], search_queries: list[str]
) -> list[JobPosting]:
    """Drop obvious Indeed noise that shares no meaningful title term."""
    query_terms = {
        term
        for query in search_queries
        for term in _words(query)
        if term not in _GENERIC_QUERY_TERMS
    }
    if not query_terms:
        return []
    return [
        posting
        for posting in postings
        if query_terms.intersection(_words(posting.title))
    ]


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", value.casefold()))


__all__ = [
    "CalibrationJobSample",
    "OnboardingCalibrationJobSampler",
    "fetch_indeed_sample",
]
