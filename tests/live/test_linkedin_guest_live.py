"""Live smoke for the LinkedIn guest source (real guest endpoints).

Never run in CI and excluded from the default suite (the ``live`` marker is
in ``addopts``'s exclusion). Run manually with::

    pytest -m live -o "addopts=" tests/live/test_linkedin_guest_live.py

Proves the two mechanisms the guest design rests on, with minimal traffic
(ONE search query paginated at ``pacing_s``, ONE posting fetch — both tests
share the single discover pass via a module cache):

1. Discover for a broad 24h-window query collects more unique ids than the
   137-id ceiling JobSpy's broken offset math capped out at, i.e. the
   owned pagination actually walks past the first ~14 pages.
2. Enriching one freshly discovered id returns a usable JD body.

The guest endpoints rate-limit per IP (429 / 999 / authwall redirect loops).
A blocked run skips with a clear message instead of failing — being blocked
today is not an adapter regression; re-run later.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jobfeed.adapters.sources._linkedin_guest_http import (
    GuestResponse,
    create_client,
    fetch,
)
from jobfeed.adapters.sources.linkedin_guest import (
    MIN_JD_CHARS,
    GuestSourceSettings,
    LinkedInGuestEnricher,
    LinkedInGuestSource,
)
from jobfeed.domain.models import JobPosting
from jobfeed.observability import get_logger
from jobfeed.ports.enrich import EnrichOutcome

pytestmark = pytest.mark.live

# The design's verified 993-id case: a broad term over the last 24 hours.
_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords=software%20engineer&location=United%20States&f_TPR=r86400"
)
# JobSpy's wrong-offset pagination stalled at ~137 unique ids per query;
# beating it proves the owned start-by-card-count pagination works live.
_PAGINATION_CAP = 137
# Page-failure statuses that mean "blocked", not "broken": 429/999 rate
# limits, plus the 0 sentinel (request errors incl. authwall redirect loops).
_BLOCK_STATUSES = frozenset({0, 429, 999})
_TIMEOUT_S = 15.0


class _RecordingLogger:
    """Real structlog logger that also records guest page-block statuses.

    Discover never raises on a blocked page — it logs
    ``guest_search_page_failed`` and ends that URL — so watching that warning
    is how the test tells "short because rate-limited" (skip) apart from
    "short because the pagination is broken" (fail).
    """

    def __init__(self) -> None:
        self._real = get_logger()
        self.block_statuses: list[int] = []

    def info(self, event: str, **kwargs: object) -> object:
        """Pass through to the real logger."""
        return self._real.info(event, **kwargs)

    def debug(self, event: str, **kwargs: object) -> object:
        """Pass through to the real logger."""
        return self._real.debug(event, **kwargs)

    def error(self, event: str, **kwargs: object) -> object:
        """Pass through to the real logger."""
        return self._real.error(event, **kwargs)

    def warning(self, event: str, **kwargs: object) -> object:
        """Record block statuses from page failures, then pass through."""
        status = kwargs.get("status")
        if (
            event == "guest_search_page_failed"
            and isinstance(status, int)
            and status in _BLOCK_STATUSES
        ):
            self.block_statuses.append(status)
        return self._real.warning(event, **kwargs)


@dataclass(frozen=True)
class _DiscoverRun:
    """One cached live discover pass: postings plus observed block statuses."""

    postings: list[JobPosting]
    block_statuses: list[int]


_cache: dict[str, _DiscoverRun] = {}


def _require_postings(run: _DiscoverRun) -> None:
    """Skip when discover was blocked empty; fail when empty with no blocks."""
    if run.postings:
        return
    if run.block_statuses:
        pytest.skip(
            "guest discover returned zero postings (block statuses: "
            f"{run.block_statuses}) — IP rate-limited; re-run later"
        )
    pytest.fail(
        "guest discover returned zero postings with zero observed page blocks"
        " — a search-card parse regression, not rate limiting"
    )


async def _discover_once() -> _DiscoverRun:
    """Run the single live discover pass, cached across both tests."""
    if "run" not in _cache:
        logger = _RecordingLogger()
        source = LinkedInGuestSource(
            settings=GuestSourceSettings(search_urls=[_SEARCH_URL]),
            logger=logger,
        )
        postings = await source.fetch_jobs({})
        logger.info(
            "guest_live_discover_done",
            unique_ids=len(postings),
            block_statuses=logger.block_statuses,
        )
        _cache["run"] = _DiscoverRun(
            postings=postings, block_statuses=logger.block_statuses
        )
    return _cache["run"]


async def _enrich_live(posting: JobPosting) -> EnrichOutcome:
    """Run the single live enrich fetch for one discovered posting."""
    logger = get_logger()
    client = create_client(None, _TIMEOUT_S)
    async with client:

        async def fetch_url(url: str) -> GuestResponse:
            return await fetch(client, url)

        enricher = LinkedInGuestEnricher(fetcher=fetch_url, logger=logger)
        outcome = await enricher.enrich(
            canonical_id=posting.canonical_id, url=posting.url
        )
    logger.info(
        "guest_live_enrich_done",
        canonical_id=posting.canonical_id,
        jd_length=len(outcome.result.jd_text) if outcome.result else None,
        is_blocked=outcome.is_blocked,
        is_gone=outcome.is_gone,
        error=outcome.error,
    )
    return outcome


class TestLinkedInGuestLive:
    """One live search query + one live enrich against the guest endpoints."""

    async def test_discover_beats_pagination_cap(self) -> None:
        """A broad 24h-window query yields more unique ids than the old cap."""
        run = await _discover_once()
        _require_postings(run)
        unique_ids = {posting.canonical_id for posting in run.postings}
        assert len(unique_ids) == len(run.postings)  # discover dedupes by id
        assert all(p.platform == "linkedin_guest" for p in run.postings)
        if len(unique_ids) <= _PAGINATION_CAP and run.block_statuses:
            pytest.skip(
                f"discover rate-limited mid-run (statuses {run.block_statuses});"
                f" only {len(unique_ids)} unique ids collected — re-run later"
            )
        assert len(unique_ids) > _PAGINATION_CAP

    async def test_enrich_one_discovered_id(self) -> None:
        """Enriching one discovered id returns a real JD body."""
        run = await _discover_once()
        _require_postings(run)
        posting = run.postings[0]
        outcome = await _enrich_live(posting)
        if outcome.is_blocked:
            pytest.skip("guest enrich blocked (429/999) — re-run later")
        if outcome.is_gone:
            pytest.fail(
                f"posting {posting.canonical_id} gone on enrich (404/410 is "
                "definitive, not transient) — endpoint drift or id-derivation "
                "bug; if a removal race, re-run"
            )
        # Authwall 200 teasers surface as jd_too_short → failure by design
        # (don't mask parse_jd regressions; the error string aids diagnosis).
        assert outcome.result is not None, f"enrich failed: {outcome.error}"
        assert len(outcome.result.jd_text) > MIN_JD_CHARS
        assert outcome.result.enrich_source == "linkedin_guest"
