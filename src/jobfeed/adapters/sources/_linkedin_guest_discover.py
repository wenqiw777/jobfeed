"""Private discover-run internals for the LinkedIn guest source.

Holds ``_GuestRun`` — the state of one ``LinkedInGuestSource.fetch_jobs``
pass (dedupe map, pacer, shared run timestamp) — and the card→``JobPosting``
mapping. Split out of ``linkedin_guest`` to keep the public facade within the
file-size gate; behavior is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from jobfeed.adapters.sources._linkedin_guest_http import (
    SearchParams,
    parse_search_params,
    search_url,
)
from jobfeed.adapters.sources._linkedin_guest_parse import (
    ParsedCard,
    parse_search_cards,
)
from jobfeed.domain.models import JobPosting

if TYPE_CHECKING:
    from jobfeed.adapters.sources.linkedin_guest import (
        AsyncSleeper,
        Fetcher,
        GuestSourceSettings,
    )
    from jobfeed.observability import JobfeedLogger

_PLATFORM = "linkedin_guest"
_HTTP_OK = 200
# The guest list endpoint refuses offsets at/after 1000; never request them.
_MAX_START = 1000


class _GuestRun:
    """State for one ``fetch_jobs`` pass: dedupe map, pacer, run timestamp.

    A fresh instance per call keeps ``LinkedInGuestSource`` reentrant and
    stamps every posting of the run with one shared ``discovered_at``.
    """

    def __init__(
        self,
        *,
        settings: GuestSourceSettings,
        logger: JobfeedLogger,
        fetcher: Fetcher,
        sleeper: AsyncSleeper,
    ) -> None:
        self._settings = settings
        self._log = logger
        self._fetch = fetcher
        self._sleep = sleeper
        self._unique: dict[str, JobPosting] = {}
        self._now = datetime.now(UTC)
        self._has_fetched = False

    async def collect(self) -> list[JobPosting]:
        """Scrape every configured search URL into a deduped posting list.

        Returns:
            Postings in first-seen order, trimmed to ``max_jobs``.
        """
        for url in self._settings.search_urls:
            if len(self._unique) >= self._settings.max_jobs:
                break
            params = parse_search_params(url)
            if not params.keywords:
                self._log.warning("guest_search_url_missing_keywords", url=url)
                continue
            await self._paginate_url(params)
        return list(self._unique.values())[: self._settings.max_jobs]

    async def _paginate_url(self, params: SearchParams) -> None:
        """Walk one URL's guest list pages, absorbing cards into the run.

        ``start`` begins at 0 and advances by the number of cards the page
        returned (duplicates included — the offset is the endpoint's, not
        ours), looping while pages stay non-empty, ``start < 1000``, and the
        unique total is below ``max_jobs``. A non-200/empty page ends this
        URL only; everything collected so far stays.

        Time complexity: O(pages) fetches per URL — the ``start < 1000``
        guard bounds it (~100 pages at the endpoint's 10-card page size) —
        plus O(cards) parsing/absorbing work overall.
        """
        start = 0
        while start < _MAX_START and len(self._unique) < self._settings.max_jobs:
            cards = await self._fetch_page(params, start)
            if not cards:
                return
            self._absorb(cards)
            start += len(cards)

    async def _fetch_page(self, params: SearchParams, start: int) -> list[ParsedCard]:
        """Fetch and parse one list page after pacing.

        Returns:
            Parsed cards, or ``[]`` to signal end-of-URL — an empty page or
            any non-200 status (429 / 999 / 4xx / the retry-exhausted 0
            sentinel), the latter logged at warning level.
        """
        await self._pace()
        page_url = search_url(params.keywords, params.location, params.f_tpr, start)
        response = await self._fetch(page_url)
        if response.status != _HTTP_OK:
            self._log.warning(
                "guest_search_page_failed",
                status=response.status,
                start=start,
                keywords=params.keywords,
            )
            return []
        return parse_search_cards(response.text)

    async def _pace(self) -> None:
        """Sleep ``pacing_s`` before every fetch except the run's first."""
        if self._has_fetched:
            await self._sleep(self._settings.pacing_s)
        self._has_fetched = True

    def _absorb(self, cards: list[ParsedCard]) -> None:
        """Union parsed cards into the dedupe map by bare job id; first wins."""
        for card in cards:
            self._unique.setdefault(card.job_id, _to_posting(card, now=self._now))


def _to_posting(card: ParsedCard, *, now: datetime) -> JobPosting:
    """Map one parsed search card to an unenriched guest JobPosting."""
    return JobPosting(
        platform=_PLATFORM,
        canonical_id=card.job_id,
        url=card.url,
        title=card.title,
        company=card.company,
        location=card.location or "",
        discovered_at=now,
        jd_text=None,
        posted_at=card.posted_at,
        enriched_at=None,
        enrich_source=None,
    )


__all__ = ["_GuestRun"]
