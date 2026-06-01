"""LinkedIn Playwright scan session: discovery plus JD enrichment."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobfeed.config import SourcesLinkedInConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality, is_jd_fresh, quality_rank
from jobfeed.ports.source import DiscoverResult, EnrichmentLookup, EnrichResult

from ._linkedin_discover import discover_linkedin_jobs
from ._linkedin_dom import human_delay, read_job_description

Sleeper = Callable[[float], Awaitable[None]]
_GOOD_RANK = quality_rank(QualityBand.GOOD)


class LinkedInScanSession:
    """One LinkedIn browser session that discovers then enriches postings.

    Discovery and enrichment share a single persistent page and the
    ``source_search_urls`` provenance map, so tier1 enrichment can reopen the
    exact search a posting came from and select that posting. The session is
    created inside the adapter's locked ``session()`` context, so both phases
    run under one enrich lock.
    """

    def __init__(
        self,
        *,
        page: Any,
        config: SourcesLinkedInConfig,
        sleeper: Sleeper,
        logger: Any,
        freshness: EnrichmentLookup | None = None,
    ) -> None:
        """Create a LinkedIn scan session.

        Args:
            page: Playwright page bound to the persistent LinkedIn profile.
            config: LinkedIn source configuration.
            sleeper: Async sleep hook; tests can inject a no-op.
            logger: Structured logger for discovery events.
            freshness: Optional read-only store probe; when a posting's JD is
                already fresh in the store, enrichment navigation is skipped.
        """
        self.page = page
        self.config = config
        self.sleeper = sleeper
        self.logger = logger
        self.freshness = freshness
        self.source_search_urls: dict[str, str] = {}
        self._tier2_used = 0

    async def discover(self, _config: dict[str, object]) -> DiscoverResult:
        """Discover LinkedIn postings under the active browser session.

        Args:
            _config: SourceSpec config placeholder; runtime config lives on self.

        Returns:
            Discovery result with postings or a reauth signal.
        """
        return await discover_linkedin_jobs(
            self.page,
            self.config,
            self.source_search_urls,
            self.sleeper,
            self.logger,
        )

    async def enrich(self, posting: JobPosting) -> EnrichResult:
        """Enrich one posting via search-pane retry, then detail page fallback.

        Args:
            posting: Discovered LinkedIn posting.

        Returns:
            Enrichment result to merge into the persisted posting.
        """
        if _is_fresh(posting):
            return _existing_result(posting, "cached-fresh")
        cached = await self._stored_fresh(posting)
        if cached is not None:
            return cached
        try:
            tier1 = await self._try_tier1(posting)
            if tier1 is not None and quality_rank(tier1.quality) >= _GOOD_RANK:
                return tier1
            return await self._try_tier2(posting, tier1)
        except Exception as exc:
            return _error_result(posting, str(exc))

    async def _stored_fresh(self, posting: JobPosting) -> EnrichResult | None:
        """Return a cached result when the store already holds a fresh JD.

        Cross-run idempotency: a posting whose stored JD is still fresh (see
        ``is_jd_fresh``) skips both tiers of browser navigation, sparing the
        per-card click / detail goto and the LinkedIn anti-bot budget.
        """
        if self.freshness is None:
            return None
        stored = await self.freshness.get_enrichment(
            platform=posting.platform,
            canonical_id=posting.canonical_id,
        )
        if stored is None or not is_jd_fresh(
            quality=stored.quality,
            jd_text=stored.jd_text,
            enriched_at=stored.enriched_at,
            now=datetime.now(UTC),
        ):
            return None
        return EnrichResult(
            jd_text=stored.jd_text or "",
            quality=stored.quality or QualityBand.MISSING,
            enrich_source="cached-fresh",
            posted_at=posting.posted_at,
        )

    async def _try_tier1(self, posting: JobPosting) -> EnrichResult | None:
        search_url = self.source_search_urls.get(posting.canonical_id)
        if search_url is None:
            return None
        # Reopen the originating search with THIS job selected so LinkedIn
        # renders its detail pane. A bare search URL would show the
        # auto-selected first result (wrong JD) or an empty pane.
        target_url = _with_current_job(search_url, posting.canonical_id)
        await self.page.goto(target_url, wait_until="domcontentloaded")
        await self.sleeper(human_delay())
        jd_text = await read_job_description(self.page)
        if not jd_text:
            return None
        return EnrichResult(
            jd_text=jd_text,
            quality=assess_quality(jd_text),
            enrich_source="linkedin_search_pane",
            posted_at=posting.posted_at,
        )

    async def _try_tier2(
        self,
        posting: JobPosting,
        fallback: EnrichResult | None,
    ) -> EnrichResult:
        if self._tier2_used >= self.config.tier2_cap:
            return fallback or _error_result(posting, "LinkedIn tier2 cap reached")
        self._tier2_used += 1
        await self.page.goto(posting.url, wait_until="domcontentloaded")
        await self.sleeper(human_delay())
        jd_text = await read_job_description(self.page)
        if not jd_text:
            return fallback or _error_result(posting, "LinkedIn detail JD missing")
        return EnrichResult(
            jd_text=jd_text,
            quality=assess_quality(jd_text),
            enrich_source="linkedin_detail",
            posted_at=posting.posted_at,
        )


def _with_current_job(search_url: str, job_id: str) -> str:
    """Return ``search_url`` with ``currentJobId`` set so one posting is selected.

    Args:
        search_url: Originating LinkedIn search URL for the posting.
        job_id: Canonical (numeric) LinkedIn job id to select.

    Returns:
        The search URL with a single ``currentJobId`` query parameter applied.
    """
    parts = urlsplit(search_url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "currentJobId"]
    query.append(("currentJobId", job_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _is_fresh(posting: JobPosting) -> bool:
    return (
        posting.enriched_at is not None
        and posting.jd_text is not None
        and quality_rank(posting.jd_quality) >= _GOOD_RANK
    )


def _existing_result(posting: JobPosting, source: str) -> EnrichResult:
    return EnrichResult(
        jd_text=posting.jd_text or "",
        quality=posting.jd_quality or QualityBand.MISSING,
        enrich_source=source,
        posted_at=posting.posted_at,
    )


def _error_result(posting: JobPosting, error: str) -> EnrichResult:
    return EnrichResult(
        jd_text=posting.jd_text or "",
        quality=posting.jd_quality or QualityBand.MISSING,
        enrich_source="error",
        error=error,
        posted_at=posting.posted_at,
    )


__all__ = ["LinkedInScanSession"]
