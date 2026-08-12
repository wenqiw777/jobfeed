"""Unit tests for StepTimer context manager and ScanService on_progress."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.models import JobPosting, PipelineRun, QualityBand, SaveJobResult
from jobfeed.domain.models_perf import StepTiming
from jobfeed.observability import _NoOpSpanWrapper
from jobfeed.services._timing import StepTimer
from jobfeed.services.scan import ScanService
from tests.support.run_leases import SuccessfulRunLeaseMixin

EXPECTED_PROGRESS_CALLS = 2


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingPerfStore:
    """Minimal store double that captures step timings."""

    def __init__(self) -> None:
        self.timings: list[StepTiming] = []

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Persist a step timing record in memory."""
        self.timings.append(timing)


class _NoOpTracer:
    """Span wrapper that does nothing."""

    @staticmethod
    def start_as_current_span(_name: str) -> _NoOpCtx:
        """Return a no-op context manager."""
        return _NoOpCtx()


class _NoOpCtx:
    """Trivial context manager for the no-op tracer."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        pass


class ScanRecordingStore(SuccessfulRunLeaseMixin):
    """Combined store double for scan + perf paths."""

    def __init__(self) -> None:
        self.jobs: list[JobPosting] = []
        self.runs: list[object] = []
        self.timings: list[StepTiming] = []

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Record a saved job and report an insert."""
        self.jobs.append(job)
        return SaveJobResult(job_id=job.canonical_id, inserted=True, updated=False)

    async def record_pipeline_run(self, run: object) -> None:
        """Record the final pipeline run."""
        self.runs.append(run)

    async def update_pipeline_run_status(self, _run: object) -> None:
        """No-op status update."""

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Record a step timing."""
        self.timings.append(timing)

    # Remaining StorePerfMixin surface so the runtime_checkable isinstance
    # in get_perf_store() recognizes this double (else scans silently
    # degrade to the no-op perf store and record nothing).
    async def record_step_timings(self, timings: list[StepTiming]) -> None:
        """Record a batch of step timings."""
        self.timings.extend(timings)

    async def get_performance_overview(self, _window_days: int) -> object:
        """Unused read surface."""
        raise NotImplementedError

    async def get_step_timings(
        self, _window_days: int, _step_type: str | None = None
    ) -> list[object]:
        """Unused read surface."""
        raise NotImplementedError

    async def get_llm_daily_stats(self, _window_days: int) -> list[object]:
        """Unused read surface."""
        raise NotImplementedError

    async def get_funnel_stats(self, _window_days: int) -> list[object]:
        """Unused read surface."""
        raise NotImplementedError


class ScanRecordingLogger:
    """Small in-memory logger for service assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> object:
        """Record an info event."""
        self.events.append((event, kwargs))
        return kwargs

    def error(self, event: str, **kwargs: object) -> object:
        """Record an error event."""
        self.events.append((event, kwargs))
        return kwargs

    def warning(self, event: str, **kwargs: object) -> object:
        """Record a warning event."""
        self.events.append((event, kwargs))
        return kwargs

    def debug(self, event: str, **kwargs: object) -> object:
        """Record a debug event."""
        self.events.append((event, kwargs))
        return kwargs


class StaticSimpleSource:
    """SimpleSource double returning configured postings."""

    def __init__(self, jobs: list[JobPosting]) -> None:
        self.jobs = jobs

    async def fetch_jobs(self, _config: dict[str, object]) -> list[JobPosting]:
        """Return the configured jobs."""
        return self.jobs


# ---------------------------------------------------------------------------
# StepTimer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_timer_records_positive_elapsed_ms() -> None:
    """StepTimer should record elapsed_ms > 0 on a successful block."""
    store = RecordingPerfStore()
    tracer = _NoOpTracer()

    async with StepTimer(store, "run-1", "source_fetch", "mock", tracer):
        pass  # simulate a fast operation

    assert len(store.timings) == 1
    t = store.timings[0]
    assert t.run_id == "run-1"
    assert t.step_type == "source_fetch"
    assert t.step_name == "mock"
    assert t.elapsed_ms > 0
    assert t.is_error is False


@pytest.mark.asyncio
async def test_step_timer_sets_is_error_on_exception() -> None:
    """StepTimer should set is_error=True and re-raise on exception."""
    store = RecordingPerfStore()
    tracer = _NoOpTracer()

    with pytest.raises(ValueError, match="boom"):
        async with StepTimer(store, "run-2", "evaluate", "llm", tracer):
            raise ValueError("boom")

    assert len(store.timings) == 1
    t = store.timings[0]
    assert t.is_error is True
    assert t.elapsed_ms > 0
    assert t.step_name == "llm"


@pytest.mark.asyncio
async def test_step_timer_works_with_noop_span_wrapper() -> None:
    """StepTimer should work with the no-op span wrapper (OTel disabled)."""
    store = RecordingPerfStore()

    async with StepTimer(store, "run-3", "digest", "email", _NoOpSpanWrapper()):
        pass

    assert len(store.timings) == 1
    assert store.timings[0].step_type == "digest"


class _FailingPerfStore:
    """Perf store whose timing INSERT always fails."""

    async def record_step_timing(self, _timing: object) -> None:
        """Simulate a transient DB failure on the timing write."""
        raise RuntimeError("timing insert failed")


@pytest.mark.asyncio
async def test_step_timer_swallows_store_write_failure() -> None:
    """A failed timing INSERT is logged, never raised into the block's caller.

    Timing is observability data: it must not be the thing that marks an
    otherwise-successful scan or evaluate run as failed.
    """
    ran = False
    timer = StepTimer(_FailingPerfStore(), "run-4", "stage", "funnel", _NoOpTracer())
    async with timer:
        ran = True
    assert ran  # no exception escaped the context manager


@pytest.mark.asyncio
async def test_step_timer_store_failure_keeps_block_exception() -> None:
    """The block's own exception survives a simultaneous store-write failure."""
    with pytest.raises(ValueError, match="boom"):
        async with StepTimer(
            _FailingPerfStore(), "run-5", "stage", "stage_a", _NoOpTracer()
        ):
            raise ValueError("boom")


# ---------------------------------------------------------------------------
# ScanService on_progress tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_progress_callback_called_per_source() -> None:
    """on_progress should be called once per source after it completes."""
    store = ScanRecordingStore()
    logger = ScanRecordingLogger()
    calls: list[PipelineRun] = []

    source_a = StaticSimpleSource([_posting("a-1")])
    source_b = StaticSimpleSource([_posting("b-1")])

    run = await ScanService(store, logger).run(
        [("alpha", source_a, {}), ("beta", source_b, {})],
        on_progress=calls.append,
    )

    assert len(calls) == EXPECTED_PROGRESS_CALLS
    # Each call receives the same PipelineRun object (mutated in place)
    assert all(c is run for c in calls)


@pytest.mark.asyncio
async def test_on_progress_none_does_not_error() -> None:
    """Passing on_progress=None should not raise."""
    store = ScanRecordingStore()
    logger = ScanRecordingLogger()

    source = StaticSimpleSource([_posting("m-1")])

    run = await ScanService(store, logger).run([("mock", source, {})])

    assert run.jobs_inserted == 1


@pytest.mark.asyncio
async def test_scan_records_step_timings() -> None:
    """ScanService.run should record step timings via the perf store."""
    store = ScanRecordingStore()
    logger = ScanRecordingLogger()

    source = StaticSimpleSource([_posting("t-1")])
    await ScanService(store, logger).run([("mock_src", source, {})])

    assert len(store.timings) == 1
    t = store.timings[0]
    assert t.step_type == "source_fetch"
    assert t.step_name == "mock_src"
    assert t.elapsed_ms > 0
    assert t.is_error is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _posting(canonical_id: str) -> JobPosting:
    return JobPosting(
        platform="mock",
        canonical_id=canonical_id,
        url=f"https://example.test/jobs/{canonical_id}",
        title="Backend Intern",
        company="Acme Corp",
        location="Remote",
        discovered_at=datetime.now(UTC),
        jd_text=None,
        jd_quality=QualityBand.MISSING,
    )
