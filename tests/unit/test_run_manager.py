"""Unit tests for RunManager service lifecycle, concurrency, and broadcast."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import PipelineRun
from jobfeed.services.run_manager import ActiveRun, RunManager

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingStore:
    """Minimal store double for RunManager tests."""

    def __init__(self) -> None:
        self.status_updates: list[tuple[str, str, datetime | None]] = []
        self._runs: list[PipelineRun] = []

    async def update_pipeline_run_status(
        self, run_id: str, status: str, finished_at: datetime | None = None
    ) -> None:
        """Record a status transition."""
        self.status_updates.append((run_id, status, finished_at))

    async def list_pipeline_runs(
        self, *, limit: int = 50, **_kw: object
    ) -> tuple[list[PipelineRun], int]:
        """Return configured runs."""
        return self._runs[:limit], len(self._runs)

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Record a pipeline run (called by ScanService/EvaluateService)."""
        self._runs.append(run)


class RecordingLogger:
    """Small in-memory logger for lifecycle assertions."""

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


class FakeScanService:
    """ScanService double that completes immediately or raises."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self._should_fail = should_fail
        self.ran = False

    async def run(
        self,
        _sources: list[object],
        on_progress: object = None,
    ) -> PipelineRun:
        """Simulate a scan run."""
        if self._should_fail:
            msg = "scan exploded"
            raise RuntimeError(msg)
        self.ran = True
        run = PipelineRun(
            run_id="fake",
            started_at=datetime.now(UTC),
            source="mock",
        )
        if on_progress is not None:
            on_progress(run)  # type: ignore[operator]
        return run


class FakeEvaluateService:
    """EvaluateService double that completes immediately or raises."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self._should_fail = should_fail
        self.ran = False

    async def run(self, **kwargs: object) -> PipelineRun:
        """Simulate an evaluate run."""
        if self._should_fail:
            msg = "evaluate exploded"
            raise RuntimeError(msg)
        self.ran = True
        run = PipelineRun(
            run_id="fake",
            started_at=datetime.now(UTC),
            source="evaluate",
        )
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress(run)  # type: ignore[operator]
        return run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_manager(
    store: RecordingStore | None = None,
    logger: RecordingLogger | None = None,
    scan_service: FakeScanService | None = None,
    eval_service: FakeEvaluateService | None = None,
) -> RunManager:
    """Build a RunManager with test doubles."""
    store = store or RecordingStore()
    logger = logger or RecordingLogger()
    scan = scan_service or FakeScanService()
    evl = eval_service or FakeEvaluateService()
    return RunManager(
        store=store,
        logger=logger,
        scan_service_factory=lambda: scan,
        evaluate_service_factory=lambda **_kw: evl,
    )


def _make_gated_scan(gate: asyncio.Event) -> object:
    """Build a scan service that blocks until gate.set()."""

    class _Gated:
        async def run(
            self, _sources: object, on_progress: object = None
        ) -> PipelineRun:
            """Block until gate is set, then call on_progress."""
            await gate.wait()
            run = PipelineRun(run_id="x", started_at=datetime.now(UTC), source="mock")
            if on_progress is not None:
                on_progress(run)  # type: ignore[operator]
            return run

    return _Gated()


# ---------------------------------------------------------------------------
# trigger_scan tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_scan_returns_run_id() -> None:
    """trigger_scan should return a non-empty run_id."""
    mgr = _build_manager()
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    assert run_id
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_scan_completes_with_succeeded_status() -> None:
    """After scan completes, active_runs empty and store records succeeded."""
    store = RecordingStore()
    mgr = _build_manager(store=store)
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    await asyncio.sleep(0.1)
    assert mgr.get_active_runs() == []
    assert any(
        rid == run_id and status == "succeeded"
        for rid, status, _ in store.status_updates
    )


@pytest.mark.asyncio
async def test_concurrent_scan_raises_conflict() -> None:
    """A second scan trigger while one is active raises RunConflictError."""
    gate = asyncio.Event()
    blocking = _make_gated_scan(gate)
    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=lambda: blocking,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    # Lock is acquired eagerly, so it is held immediately after trigger
    await mgr.trigger_scan([("mock", object(), {})])

    with pytest.raises(RunConflictError):
        await mgr.trigger_scan([("mock", object(), {})])

    gate.set()
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# trigger_evaluate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_evaluate_returns_run_id() -> None:
    """trigger_evaluate should return a non-empty run_id."""
    mgr = _build_manager()
    run_id = await mgr.trigger_evaluate(stage="both", corpus="unrated")
    assert run_id
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_concurrent_evaluate_raises_conflict() -> None:
    """A second evaluate trigger while one is active raises RunConflictError."""
    gate = asyncio.Event()

    class _BlockingEval:
        """Eval service that blocks until gate is set."""

        async def run(self, **_kwargs: object) -> PipelineRun:
            """Block until gate is set."""
            await gate.wait()
            return PipelineRun(
                run_id="x", started_at=datetime.now(UTC), source="evaluate"
            )

    blocking = _BlockingEval()
    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=FakeScanService,
        evaluate_service_factory=lambda **_kw: blocking,  # type: ignore[arg-type]
    )
    await mgr.trigger_evaluate(stage="both")

    with pytest.raises(RunConflictError):
        await mgr.trigger_evaluate(stage="both")

    gate.set()
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_exception_sets_failed_status() -> None:
    """A service exception should mark the run as failed."""
    store = RecordingStore()
    logger = RecordingLogger()
    failing = FakeScanService(should_fail=True)
    mgr = _build_manager(store=store, logger=logger, scan_service=failing)
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    await asyncio.sleep(0.1)

    assert any(
        rid == run_id and status == "failed" for rid, status, _ in store.status_updates
    )
    assert any(ev == "run_failed" for ev, _ in logger.events)
    assert mgr.get_active_runs() == []


# ---------------------------------------------------------------------------
# Subscribe / broadcast tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_yields_progress_events() -> None:
    """subscribe should yield events from progress broadcasts."""
    gate = asyncio.Event()
    blocking = _make_gated_scan(gate)
    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=lambda: blocking,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    run_id = await mgr.trigger_scan([("mock", object(), {})])

    received: list[PipelineRun] = []

    async def _collect() -> None:
        async for event in mgr.subscribe(run_id):
            received.append(event)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0.01)  # let subscriber register
    gate.set()
    await asyncio.sleep(0.1)  # let run complete
    await task

    # on_progress fires once, plus _finish_run broadcasts once
    assert len(received) >= 1


@pytest.mark.asyncio
async def test_multiple_subscribers_receive_events() -> None:
    """Multiple subscribers on the same run_id all receive events."""
    gate = asyncio.Event()
    blocking = _make_gated_scan(gate)
    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=lambda: blocking,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    run_id = await mgr.trigger_scan([("mock", object(), {})])

    received_a: list[PipelineRun] = []
    received_b: list[PipelineRun] = []

    async def _collect(target: list[PipelineRun]) -> None:
        async for event in mgr.subscribe(run_id):
            target.append(event)

    task_a = asyncio.create_task(_collect(received_a))
    task_b = asyncio.create_task(_collect(received_b))
    await asyncio.sleep(0.01)  # let subscribers register
    gate.set()
    await asyncio.sleep(0.1)  # let run complete
    await asyncio.gather(task_a, task_b)

    assert len(received_a) == len(received_b)
    assert len(received_a) >= 1


# ---------------------------------------------------------------------------
# recover_stale_runs tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_stale_runs_transitions_running_to_failed() -> None:
    """recover_stale_runs should mark 'running' pipeline runs as 'failed'."""
    store = RecordingStore()
    stale_run = PipelineRun(
        run_id="stale-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="scan",
        status="running",
    )
    done_run = PipelineRun(
        run_id="done-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="evaluate",
        status="succeeded",
    )
    store._runs = [stale_run, done_run]

    logger = RecordingLogger()
    mgr = _build_manager(store=store, logger=logger)
    recovered = await mgr.recover_stale_runs()

    assert recovered == 1
    assert any(
        rid == "stale-1" and status == "failed"
        for rid, status, _ in store.status_updates
    )
    assert not any(rid == "done-1" for rid, _, _ in store.status_updates)


@pytest.mark.asyncio
async def test_recover_stale_runs_returns_zero_when_none_stale() -> None:
    """recover_stale_runs returns 0 when no runs are stuck."""
    store = RecordingStore()
    store._runs = []
    mgr = _build_manager(store=store)
    assert await mgr.recover_stale_runs() == 0


# ---------------------------------------------------------------------------
# get_active_runs tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_runs_returns_active_during_execution() -> None:
    """get_active_runs should return runs that are still executing."""
    gate = asyncio.Event()
    blocking = _make_gated_scan(gate)
    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=lambda: blocking,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    run_id = await mgr.trigger_scan([("mock", object(), {})])

    active = mgr.get_active_runs()
    assert len(active) == 1
    assert active[0].run_id == run_id
    assert isinstance(active[0], ActiveRun)

    gate.set()
    await asyncio.sleep(0.05)
    assert mgr.get_active_runs() == []
