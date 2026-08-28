"""Unit tests for the paced, resumable EnrichService pass.

Covers the full outcome matrix with scripted fakes: sequential successes
recorded with ``enrich_source="linkedin_guest"`` (posted_at forwarded),
``min_interval_s`` pacing between attempts (none before the first, none
trailing), exponential backoff that replaces the inter-request gap and
re-queues the blocked row, the exactly-N consecutive-block early stop
(remaining rows untouched and resumable), the consecutive counter reset on
success, gone rows closed with the platform reason, error outcomes and
raised exceptions skipped without killing the pass, the empty queue, and
batch_limit forwarding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from jobfeed.domain.models import QualityBand, UnenrichedJob
from jobfeed.ports.enrich import EnrichOutcome
from jobfeed.ports.source import EnrichResult
from jobfeed.services.enrich import EnrichPacing, EnrichService, EnrichSummary

_BATCH_LIMIT = 25
_POSTED_AT = datetime(2026, 5, 28, 9, 30, tzinfo=UTC)
_JD_TEXT = "Design, build, and operate distributed ingestion pipelines."

_BLOCKED = EnrichOutcome(is_blocked=True)
_GONE = EnrichOutcome(is_gone=True)
_ERROR = EnrichOutcome(error="status:500")


def _success(posted_at: datetime | None = None) -> EnrichOutcome:
    """Build a successful outcome carrying a full-quality JD result."""
    return EnrichOutcome(
        result=EnrichResult(
            jd_text=_JD_TEXT,
            quality=QualityBand.FULL,
            enrich_source="linkedin_guest",
            posted_at=posted_at,
        )
    )


def _row(n: int) -> UnenrichedJob:
    """Build the n-th un-enriched row fixture."""
    return UnenrichedJob(
        job_id=f"job-{n}",
        canonical_id=f"li-{n}",
        url=f"https://www.linkedin.com/jobs/view/{n}",
    )


@dataclass(kw_only=True)
class _FakeStore:
    """In-memory store recording every enrichment, closure, and listing."""

    rows: list[UnenrichedJob] = field(default_factory=list)
    list_calls: list[tuple[str, int]] = field(default_factory=list)
    enrichments: list[dict[str, object]] = field(default_factory=list)
    closures: list[dict[str, object]] = field(default_factory=list)
    listed_job_ids: list[str] | None = None

    async def list_unenriched_jobs(
        self,
        *,
        platform: str,
        limit: int,
        job_ids: list[str] | None = None,
    ) -> list[UnenrichedJob]:
        self.list_calls.append((platform, limit))
        self.listed_job_ids = job_ids
        wanted = set(job_ids) if job_ids is not None else None
        rows = [row for row in self.rows if wanted is None or row.job_id in wanted]
        return rows[:limit]

    async def record_enrichment(self, **kwargs: object) -> None:
        self.enrichments.append(kwargs)

    async def mark_job_closed(
        self, *, job_id: str, closed_at: datetime, reason: str | None = None
    ) -> None:
        self.closures.append(
            {"job_id": job_id, "closed_at": closed_at, "reason": reason}
        )


@dataclass
class _ScriptedEnricher:
    """Enricher returning (or raising) scripted items in call order."""

    outcomes: list[EnrichOutcome | Exception]
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def enrich(self, *, canonical_id: str, url: str) -> EnrichOutcome:
        self.calls.append((canonical_id, url))
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@dataclass
class _RecordingSleeper:
    """Injected sleep recording every requested delay."""

    delays: list[float] = field(default_factory=list)

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _FakeLogger:
    """Logger capturing (level, event, kwargs) tuples."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.events.append(("info", event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append(("warning", event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:
        self.events.append(("error", event, kwargs))


def _build(
    rows: list[UnenrichedJob],
    outcomes: list[EnrichOutcome | Exception],
    pacing: EnrichPacing | None = None,
    logger: _FakeLogger | None = None,
) -> tuple[EnrichService, _FakeStore, _ScriptedEnricher, _RecordingSleeper]:
    """Wire a service over fakes and return the recording collaborators."""
    store = _FakeStore(rows=rows)
    enricher = _ScriptedEnricher(outcomes=outcomes)
    sleeper = _RecordingSleeper()
    service = EnrichService(
        enricher=enricher,
        store=store,
        logger=logger if logger is not None else _FakeLogger(),
        sleeper=sleeper,
        pacing=pacing,
    )
    return service, store, enricher, sleeper


async def test_all_successes_recorded_with_linkedin_guest_source() -> None:
    rows = [_row(1), _row(2), _row(3)]
    service, store, _, _ = _build(rows, [_success(), _success(), _success()])

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=3, closed=0, blocked=0, skipped=0, stopped_early=False
    )
    assert [e["job_id"] for e in store.enrichments] == ["job-1", "job-2", "job-3"]
    assert all(e["enrich_source"] == "linkedin_guest" for e in store.enrichments)
    assert all(e["jd_quality"] == QualityBand.FULL.value for e in store.enrichments)
    assert all(e["jd_text"] == _JD_TEXT for e in store.enrichments)


async def test_progress_reports_current_listing_and_completed_count() -> None:
    """The caller can render live status during a paced enrich pass."""
    rows = [_row(1), _row(2)]
    service, _, _, _ = _build(rows, [_success(), _success()])
    events = []

    await service.run(
        platform="linkedin_guest",
        batch_limit=_BATCH_LIMIT,
        on_progress=events.append,
    )

    assert events[0].total == len(rows)
    assert events[0].processed == 0
    assert any(event.current_job_id == "li-1" for event in events)
    assert events[-1].processed == len(rows)
    assert events[-1].current_job_id is None


async def test_posted_at_forwarded_to_record_enrichment() -> None:
    service, store, _, _ = _build([_row(1)], [_success(posted_at=_POSTED_AT)])

    await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert store.enrichments[0]["posted_at"] == _POSTED_AT


async def test_posted_at_none_forwarded_when_result_has_none() -> None:
    service, store, _, _ = _build([_row(1)], [_success()])

    await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert store.enrichments[0]["posted_at"] is None


async def test_min_interval_sleep_between_attempts_only() -> None:
    rows = [_row(1), _row(2), _row(3)]
    service, _, _, sleeper = _build(rows, [_success(), _success(), _success()])

    await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert sleeper.delays == [1.0, 1.0]


async def test_blocked_row_backs_off_requeues_and_retries() -> None:
    service, store, enricher, sleeper = _build(
        [_row(1)], [_BLOCKED, _BLOCKED, _success()]
    )

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    # Backoff replaces the inter-request gap: 5, 10 — no 1.0 entries.
    assert sleeper.delays == [5.0, 10.0]
    # The blocked row was re-queued (same row attempted thrice), recorded once.
    assert [c[0] for c in enricher.calls] == ["li-1", "li-1", "li-1"]
    assert [e["job_id"] for e in store.enrichments] == ["job-1"]
    assert summary == EnrichSummary(
        enriched=1, closed=0, blocked=2, skipped=0, stopped_early=False
    )


async def test_backoff_delay_capped_at_max_backoff() -> None:
    pacing = EnrichPacing(
        base_backoff_s=5.0, max_backoff_s=8.0, max_consecutive_blocks=4
    )
    service, _, _, sleeper = _build(
        [_row(1)], [_BLOCKED, _BLOCKED, _BLOCKED, _success()], pacing=pacing
    )

    await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert sleeper.delays == [5.0, 8.0, 8.0]


async def test_stops_after_max_consecutive_blocks_leaving_rest_untouched() -> None:
    rows = [_row(1), _row(2)]
    logger = _FakeLogger()
    service, store, enricher, sleeper = _build(
        rows, [_BLOCKED, _BLOCKED, _BLOCKED], logger=logger
    )

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=0, closed=0, blocked=3, skipped=0, stopped_early=True
    )
    # Only the first row was ever attempted; row 2 is untouched and resumable.
    assert [c[0] for c in enricher.calls] == ["li-1", "li-1", "li-1"]
    assert store.enrichments == []
    assert store.closures == []
    # No backoff sleep after the stopping block (only the first two backoffs).
    assert sleeper.delays == [5.0, 10.0]
    # The early stop reports the re-queued row plus the untouched remainder.
    assert (
        "warning",
        "enrich_pass_stopped_early",
        {"job_id": "job-1", "consecutive": 3, "remaining": 2},
    ) in logger.events


async def test_success_resets_consecutive_block_counter() -> None:
    pacing = EnrichPacing(max_consecutive_blocks=2)
    rows = [_row(1), _row(2)]
    outcomes: list[EnrichOutcome | Exception] = [
        _BLOCKED,  # row 1: consecutive=1
        _success(),  # row 1 retry succeeds: counter resets
        _BLOCKED,  # row 2: consecutive=1 again (not 2 — no stop)
        _success(),  # row 2 retry succeeds
    ]
    service, store, _, sleeper = _build(rows, outcomes, pacing=pacing)

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=2, closed=0, blocked=2, skipped=0, stopped_early=False
    )
    assert [e["job_id"] for e in store.enrichments] == ["job-1", "job-2"]
    # Backoff replaces the gap for its slot, then the success re-arms the
    # min-interval sleep before row 2's first attempt: 5, 1, 5.
    assert sleeper.delays == [5.0, 1.0, 5.0]


async def test_gone_row_marked_closed_and_pass_continues() -> None:
    rows = [_row(1), _row(2)]
    service, store, _, _ = _build(rows, [_GONE, _success()])

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=1, closed=1, blocked=0, skipped=0, stopped_early=False
    )
    assert [c["job_id"] for c in store.closures] == ["job-1"]
    assert [c["reason"] for c in store.closures] == ["gone:linkedin_guest"]
    assert [e["job_id"] for e in store.enrichments] == ["job-2"]


async def test_error_outcome_skips_row_and_continues() -> None:
    rows = [_row(1), _row(2)]
    service, store, _, _ = _build(rows, [_ERROR, _success()])

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=1, closed=0, blocked=0, skipped=1, stopped_early=False
    )
    assert [e["job_id"] for e in store.enrichments] == ["job-2"]
    assert store.closures == []


async def test_enricher_exception_counts_as_skip_and_pass_survives() -> None:
    rows = [_row(1), _row(2)]
    service, store, _, _ = _build(rows, [RuntimeError("boom"), _success()])

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=1, closed=0, blocked=0, skipped=1, stopped_early=False
    )
    assert [e["job_id"] for e in store.enrichments] == ["job-2"]


async def test_empty_queue_returns_zero_summary_without_sleeping() -> None:
    service, _, enricher, sleeper = _build([], [])

    summary = await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert summary == EnrichSummary(
        enriched=0, closed=0, blocked=0, skipped=0, stopped_early=False
    )
    assert enricher.calls == []
    assert sleeper.delays == []


async def test_platform_and_batch_limit_forwarded_to_store() -> None:
    service, store, _, _ = _build([], [])

    await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert store.list_calls == [("linkedin_guest", _BATCH_LIMIT)]


async def test_custom_min_interval_used_for_pacing() -> None:
    pacing = EnrichPacing(min_interval_s=0.25)
    rows = [_row(1), _row(2)]
    service, _, _, sleeper = _build(rows, [_success(), _success()], pacing=pacing)

    await service.run(platform="linkedin_guest", batch_limit=_BATCH_LIMIT)

    assert sleeper.delays == [0.25]
