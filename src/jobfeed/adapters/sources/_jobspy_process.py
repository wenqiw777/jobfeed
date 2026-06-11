"""JobSpy subprocess isolation + async fan-out (orchestration, no pandas).

This module owns the machinery that runs ``_jobspy.scrape`` *safely at scale*:

* ``scrape_urls`` — the shared async fan-out both JobSpy sources
  (``indeed_jobspy``, ``linkedin_jobspy``) reuse: each URL is scraped off the
  event loop, with per-URL errors contained so one bad URL never aborts the rest.
* the ``spawn`` child-process harness — each synchronous ``scrape`` runs in a
  fresh child so a configured timeout can stop a hung JobSpy call, with the
  result drained from the queue BEFORE joining the child (see
  ``_await_scrape_outcome`` for why join-before-drain would mis-time-out a large
  successful scrape).

Nothing here touches pandas / jobspy / tls-client types — it deals only in the
pure ``_ScrapeRequest`` / ``_ScrapeProcessOutcome`` / ``JobPosting`` types and
delegates the one jobspy-touching call to ``_jobspy.scrape``. Split out of
``_jobspy.py`` so the pandas-confined core stays small and the orchestration
layer is independently testable (and both stay under the 300-line gate).
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from multiprocessing.queues import Queue
from queue import Empty
from typing import Any

from jobfeed.adapters.sources._jobspy import ScrapeConfig, scrape
from jobfeed.domain.models import JobPosting
from jobfeed.observability import JobfeedLogger

_PROCESS_START_METHOD = "spawn"
_PROCESS_KILL_GRACE_S = 1.0
_PROCESS_RESULT_GRACE_S = 0.5


@dataclass(frozen=True, kw_only=True)
class _ScrapeRequest:
    site_name: str
    platform: str
    search_url: str
    max_jobs: int
    hours_old: int | None
    country_indeed: str | None
    discovered_at: datetime


@dataclass(frozen=True, kw_only=True)
class _ScrapeProcessOutcome:
    postings: list[JobPosting]
    error: str | None = None
    is_timed_out: bool = False


async def scrape_urls(  # noqa: PLR0913 - shared loop needs each scrape input
    *,
    site_name: str,
    platform: str,
    search_urls: list[str],
    max_jobs: int,
    hours_old: int | None,
    timeout_s: float,
    max_concurrent: int,
    logger: JobfeedLogger,
    discovered_at: datetime,
    country_indeed: str | None = None,
    repeat: int = 1,
) -> list[JobPosting]:
    """Scrape every search URL off the event loop, containing per-URL errors.

    Shared by both JobSpy sources (Indeed + LinkedIn). Each URL's synchronous
    ``scrape`` runs in a child process while the parent waits from a bounded
    worker thread, so the event loop is never blocked and a hung JobSpy scrape
    can be terminated. A failure on one URL is logged and skipped so the
    remaining URLs still contribute their postings.

    Args:
        site_name: JobSpy site passed through to ``scrape``.
        platform: Platform tag passed through to ``scrape`` (and onto postings).
        search_urls: Search URLs to scrape, in order.
        max_jobs: Per-URL cap.
        hours_old: Freshness override (see ``scrape``).
        timeout_s: Per-URL wall-clock timeout for the blocking JobSpy call.
        max_concurrent: Maximum number of URLs scraped concurrently.
        logger: Structured logger for contained per-URL failures.
        discovered_at: Scan-start timestamp stamped on every posting.
        country_indeed: JobSpy country selector for Indeed searches.
        repeat: Times to re-run each URL; draws are unioned by canonical_id to
            recover postings the non-deterministic backend drops on a single pass.

    Returns:
        Aggregated postings across all URLs that did not fail, deduped by
        canonical_id (so repeated draws of the same posting collapse to one).
    """
    sem = asyncio.Semaphore(max_concurrent)
    batches = await asyncio.gather(
        *[
            _scrape_one_url(
                sem=sem,
                site_name=site_name,
                platform=platform,
                url=url,
                max_jobs=max_jobs,
                hours_old=hours_old,
                timeout_s=timeout_s,
                logger=logger,
                discovered_at=discovered_at,
                country_indeed=country_indeed,
            )
            for url in search_urls
            for _ in range(repeat)
        ]
    )
    return _dedupe_by_canonical_id(posting for batch in batches for posting in batch)


def _dedupe_by_canonical_id(postings: Iterable[JobPosting]) -> list[JobPosting]:
    """Union postings by (platform, canonical_id), keeping the first occurrence.

    ``repeat`` issues several non-deterministic draws per URL, so the same
    posting can recur across draws; collapsing duplicates here yields the union a
    single pass would miss. O(n) over the combined draws (one set membership test
    per posting).
    """
    seen: set[tuple[str, str]] = set()
    unique: list[JobPosting] = []
    for posting in postings:
        key = (posting.platform, posting.canonical_id)
        if key not in seen:
            seen.add(key)
            unique.append(posting)
    return unique


def _run_scrape_process(
    request: _ScrapeRequest, timeout_s: float
) -> _ScrapeProcessOutcome:
    ctx: Any = mp.get_context(_PROCESS_START_METHOD)
    result_queue: Queue[_ScrapeProcessOutcome] = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_scrape_child_main,
        args=(request, result_queue),
        daemon=True,
    )
    try:
        process.start()
        return _await_scrape_outcome(process, result_queue, timeout_s)
    finally:
        result_queue.close()
        result_queue.join_thread()


def _scrape_child_main(
    request: _ScrapeRequest, result_queue: Queue[_ScrapeProcessOutcome]
) -> None:
    try:
        postings = scrape(
            site_name=request.site_name,
            platform=request.platform,
            search_url=request.search_url,
            config=ScrapeConfig(
                max_jobs=request.max_jobs,
                hours_old=request.hours_old,
                country_indeed=request.country_indeed,
            ),
            discovered_at=request.discovered_at,
        )
    except Exception as exc:  # contain every child-side jobspy/tls-client failure
        result_queue.put(_ScrapeProcessOutcome(postings=[], error=str(exc)))
        return
    result_queue.put(_ScrapeProcessOutcome(postings=postings))


def _stop_process(process: Any) -> None:
    process.terminate()
    process.join(_PROCESS_KILL_GRACE_S)
    if not process.is_alive():
        return
    process.kill()
    process.join(_PROCESS_KILL_GRACE_S)


def _await_scrape_outcome(
    process: Any, result_queue: Queue[_ScrapeProcessOutcome], timeout_s: float
) -> _ScrapeProcessOutcome:
    """Read the child's result, draining the queue BEFORE joining the process.

    A ``multiprocessing.Queue`` child stays alive until the parent reads what it
    ``put`` — its feeder thread blocks flushing a large payload through the pipe.
    Joining first would let a successful large scrape (many rows / big inline
    JDs) look like a timeout and get killed. Reading first unblocks the feeder so
    the child can exit; ``timeout_s`` now bounds the *result wait*, with the same
    wall-clock budget the old ``join(timeout_s)`` used.
    """
    try:
        result = result_queue.get(timeout=timeout_s)
    except Empty:
        return _stopped_outcome(process)
    process.join(_PROCESS_RESULT_GRACE_S)
    if process.is_alive():
        _stop_process(process)
    return _coerce_outcome(result)


def _stopped_outcome(process: Any) -> _ScrapeProcessOutcome:
    """Stop a child that delivered nothing, distinguishing a crash from a hang."""
    is_crashed = not process.is_alive()
    exitcode = getattr(process, "exitcode", None)
    _stop_process(process)
    if is_crashed:
        return _ScrapeProcessOutcome(
            postings=[],
            error=f"jobspy child exited without result (exitcode={exitcode})",
        )
    return _ScrapeProcessOutcome(postings=[], is_timed_out=True)


def _coerce_outcome(result: object) -> _ScrapeProcessOutcome:
    if isinstance(result, _ScrapeProcessOutcome):
        return result
    return _ScrapeProcessOutcome(
        postings=[],
        error=f"jobspy child returned unexpected result type: {type(result).__name__}",
    )


async def _scrape_one_url(  # noqa: PLR0913 - carries the shared scrape contract
    *,
    sem: asyncio.Semaphore,
    site_name: str,
    platform: str,
    url: str,
    max_jobs: int,
    hours_old: int | None,
    timeout_s: float,
    logger: JobfeedLogger,
    discovered_at: datetime,
    country_indeed: str | None,
) -> list[JobPosting]:
    async with sem:
        request = _ScrapeRequest(
            site_name=site_name,
            platform=platform,
            search_url=url,
            max_jobs=max_jobs,
            hours_old=hours_old,
            country_indeed=country_indeed,
            discovered_at=discovered_at,
        )
        try:
            outcome = await asyncio.to_thread(_run_scrape_process, request, timeout_s)
        except Exception as exc:
            logger.warning(
                "jobspy_scrape_failed", site=site_name, url=url, error=str(exc)
            )
            return []
        if outcome.is_timed_out:
            logger.warning(
                "jobspy_scrape_timed_out",
                site=site_name,
                url=url,
                timeout_s=timeout_s,
            )
            return []
        if outcome.error is not None:
            logger.warning(
                "jobspy_scrape_failed", site=site_name, url=url, error=outcome.error
            )
            return []
        return outcome.postings


__all__ = ["scrape_urls"]
