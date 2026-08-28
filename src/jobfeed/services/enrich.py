"""Enrich service: paced, resumable per-row JD enrichment with backoff."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.domain.models import UnenrichedJob
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.enrich import EnrichOutcome, JobEnricher
from jobfeed.ports.source import EnrichResult
from jobfeed.ports.store_ops import StoreOpsMixin

AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class EnrichProgress:
    """One live snapshot of a paced enrichment pass."""

    platform: str
    total: int
    processed: int
    current_job_id: str | None


EnrichProgressCallback = Callable[[EnrichProgress], None]


@dataclass(frozen=True, kw_only=True)
class EnrichPacing:
    """Pacing and backoff knobs for one enrichment pass.

    ``min_interval_s`` is the gap slept BETWEEN consecutive enrich attempts
    (token-bucket refill of roughly one request per second by default) — not
    before the first attempt and never trailing; Task 8 maps the configured
    ``pacing_s`` onto it. After the k-th consecutive blocked attempt the
    service sleeps ``base_backoff_s * 2**(k - 1)`` capped at
    ``max_backoff_s``; that backoff REPLACES the inter-request gap for that
    slot (it does not add to it). Blocked attempts count as attempts for
    pacing purposes. After ``max_consecutive_blocks`` blocks in a row the
    pass stops early.
    """

    min_interval_s: float = 1.0
    base_backoff_s: float = 5.0
    max_backoff_s: float = 60.0
    max_consecutive_blocks: int = 3


@dataclass(frozen=True, kw_only=True)
class EnrichSummary:
    """Counters for one enrichment pass; ``blocked`` counts block events."""

    enriched: int
    closed: int
    blocked: int
    skipped: int
    stopped_early: bool


@dataclass(kw_only=True)
class _PassState:
    """Mutable bookkeeping for a single enrichment pass."""

    platform: str
    queue: deque[UnenrichedJob]
    enriched: int = 0
    closed: int = 0
    blocked: int = 0
    skipped: int = 0
    consecutive_blocks: int = 0
    needs_gap_sleep: bool = False
    stopped_early: bool = False


class EnrichService:
    """Application service for the paced per-posting JD enrichment pass."""

    def __init__(
        self,
        *,
        enricher: JobEnricher,
        store: StoreOpsMixin,
        logger: JobfeedLogger,
        sleeper: AsyncSleeper = asyncio.sleep,
        pacing: EnrichPacing | None = None,
    ) -> None:
        """Create an enrich service with injected ports.

        Args:
            enricher: Per-posting JD fetcher with block/gone classification.
            store: Persistence port for the unenriched listing, enrichment
                stamps, and gone-row closure.
            logger: Structured logger for enrichment events.
            sleeper: Async pacing hook; tests inject a recorder.
            pacing: Pacing and backoff knobs; defaults to ``EnrichPacing()``.
        """
        self.enricher = enricher
        self.store = store
        self.logger = logger
        self._sleep = sleeper
        self.pacing = pacing if pacing is not None else EnrichPacing()

    async def run(
        self,
        *,
        platform: str,
        batch_limit: int,
        job_ids: list[str] | None = None,
        on_progress: EnrichProgressCallback | None = None,
    ) -> EnrichSummary:
        """Enrich up to ``batch_limit`` un-enriched rows sequentially.

        A blocked attempt re-queues its row (the row is not consumed) and
        backs off exponentially; after ``max_consecutive_blocks`` blocks in
        a row the pass stops early — the IP is in cooldown and the remaining
        rows stay un-enriched for the next run. Gone rows are closed; error
        rows (including raised enricher exceptions) are skipped and left
        un-enriched for a future pass.

        Args:
            platform: Source platform whose rows should be enriched.
            batch_limit: Maximum rows loaded for this pass.

        Returns:
            Counters for the pass.
        """
        rows = await self.store.list_unenriched_jobs(
            platform=platform,
            limit=batch_limit,
            job_ids=job_ids,
        )
        self.logger.info("enrich_pass_started", platform=platform, queued=len(rows))
        state = _PassState(platform=platform, queue=deque(rows))
        self._report_progress(state, len(rows), on_progress)
        while state.queue and not state.stopped_early:
            await self._pace(state)
            row = state.queue.popleft()
            self._report_progress(
                state, len(rows), on_progress, current_job_id=row.canonical_id
            )
            outcome = await self._attempt(row)
            await self._handle_outcome(state, row, outcome)
            self._report_progress(state, len(rows), on_progress)
        return self._summarize(state)

    @staticmethod
    def _report_progress(
        state: _PassState,
        total: int,
        on_progress: EnrichProgressCallback | None,
        *,
        current_job_id: str | None = None,
    ) -> None:
        """Notify observers without making enrichment dependent on telemetry."""
        if on_progress is None:
            return
        on_progress(
            EnrichProgress(
                platform=state.platform,
                total=total,
                processed=state.enriched + state.closed + state.skipped,
                current_job_id=current_job_id,
            )
        )

    async def _pace(self, state: _PassState) -> None:
        if state.needs_gap_sleep:
            await self._sleep(self.pacing.min_interval_s)
        state.needs_gap_sleep = True

    async def _attempt(self, row: UnenrichedJob) -> EnrichOutcome:
        try:
            return await self.enricher.enrich(
                canonical_id=row.canonical_id, url=row.url
            )
        except Exception as exc:
            return EnrichOutcome(error=f"{type(exc).__name__}: {exc}")

    async def _handle_outcome(
        self, state: _PassState, row: UnenrichedJob, outcome: EnrichOutcome
    ) -> None:
        if outcome.result is not None:
            await self._record_success(state, row, outcome.result)
            return
        if outcome.is_gone:
            await self._record_gone(state, row)
            return
        if outcome.is_blocked:
            await self._back_off(state, row)
            return
        self._skip_error(state, row, outcome.error)

    async def _record_success(
        self, state: _PassState, row: UnenrichedJob, result: EnrichResult
    ) -> None:
        await self.store.record_enrichment(
            job_id=row.job_id,
            jd_text=result.jd_text,
            jd_quality=result.quality.value,
            enriched_at=datetime.now(UTC),
            enrich_source=result.enrich_source,
            posted_at=result.posted_at,
        )
        state.enriched += 1
        state.consecutive_blocks = 0

    async def _record_gone(self, state: _PassState, row: UnenrichedJob) -> None:
        # Closure convention elsewhere is "gone:{status}:{vendor}", but
        # EnrichOutcome collapses 404/410 into is_gone, so the platform-level
        # reason deliberately omits the status segment.
        await self.store.mark_job_closed(
            job_id=row.job_id,
            closed_at=datetime.now(UTC),
            reason=f"gone:{state.platform}",
        )
        state.closed += 1
        state.consecutive_blocks = 0

    async def _back_off(self, state: _PassState, row: UnenrichedJob) -> None:
        state.blocked += 1
        state.consecutive_blocks += 1
        state.queue.appendleft(row)
        if state.consecutive_blocks >= self.pacing.max_consecutive_blocks:
            state.stopped_early = True
            self.logger.warning(
                "enrich_pass_stopped_early",
                job_id=row.job_id,
                consecutive=state.consecutive_blocks,
                remaining=len(state.queue),
            )
            return
        delay = self._backoff_delay(state.consecutive_blocks)
        self.logger.warning(
            "enrich_backoff",
            job_id=row.job_id,
            delay_s=delay,
            consecutive=state.consecutive_blocks,
        )
        await self._sleep(delay)
        state.needs_gap_sleep = False

    def _backoff_delay(self, consecutive: int) -> float:
        scaled = self.pacing.base_backoff_s * 2.0 ** (consecutive - 1)
        return min(scaled, self.pacing.max_backoff_s)

    def _skip_error(
        self, state: _PassState, row: UnenrichedJob, error: str | None
    ) -> None:
        state.skipped += 1
        state.consecutive_blocks = 0
        self.logger.warning("enrich_row_error", job_id=row.job_id, error=error)

    def _summarize(self, state: _PassState) -> EnrichSummary:
        summary = EnrichSummary(
            enriched=state.enriched,
            closed=state.closed,
            blocked=state.blocked,
            skipped=state.skipped,
            stopped_early=state.stopped_early,
        )
        self.logger.info(
            "enrich_pass_completed",
            platform=state.platform,
            enriched=summary.enriched,
            closed=summary.closed,
            blocked=summary.blocked,
            skipped=summary.skipped,
            stopped_early=summary.stopped_early,
        )
        return summary


__all__ = [
    "EnrichPacing",
    "EnrichProgress",
    "EnrichProgressCallback",
    "EnrichService",
    "EnrichSummary",
]
