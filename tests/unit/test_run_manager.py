"""Unit tests for RunManager service lifecycle, concurrency, and broadcast."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

from jobfeed.domain.errors import RunConflictError, SourceConfigError
from jobfeed.domain.models import PipelineRun
from jobfeed.services.run_manager import RUN_DONE, ActiveRun, RunManager
from jobfeed.services.scan import SourceSpec

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class RecordingStore:
    """Minimal store double for RunManager tests."""

    def __init__(self) -> None:
        self.status_updates: list[tuple[str, str]] = []
        self._runs: list[PipelineRun] = []
        self.lease_calls: list[str] = []
        self.legacy_calls: list[str] = []
        self.finalize_result = True

    async def update_pipeline_run_status(self, run: PipelineRun) -> None:
        """Record a status transition."""
        self.legacy_calls.append("update")
        self.status_updates.append((run.run_id, run.status))

    async def list_pipeline_runs(
        self, *, limit: int = 50, **_kw: object
    ) -> tuple[list[PipelineRun], int]:
        """Return configured runs."""
        return self._runs[:limit], len(self._runs)

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Record a pipeline run (called by ScanService/EvaluateService)."""
        self.legacy_calls.append("record")
        self._runs.append(run)

    async def start_run_with_lease(self, run: PipelineRun, **_kwargs: object) -> int:
        """Atomically record a run with a new fencing generation."""
        self.lease_calls.append("start")
        self._runs.append(run)
        return 1

    async def renew_run_lease(self, **_kwargs: object) -> bool:
        """Keep a test run leased."""
        self.lease_calls.append("renew")
        return True

    async def finalize_run_with_lease(
        self, run: PipelineRun, **_kwargs: object
    ) -> bool:
        """Record a fenced terminal transition."""
        self.lease_calls.append("finalize")
        if self.finalize_result:
            self.status_updates.append((run.run_id, run.status))
        return self.finalize_result

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Return a recorded run by identity, if any."""
        for run in self._runs:
            if run.run_id == run_id:
                return run
        return None


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
        run: PipelineRun | None = None,
        lease_session: object | None = None,
    ) -> PipelineRun:
        """Simulate a scan run."""
        if self._should_fail:
            msg = "scan exploded"
            raise RuntimeError(msg)
        self.ran = True
        if lease_session is not None:
            run = lease_session.run  # type: ignore[attr-defined]
        elif run is None:
            run = PipelineRun(
                run_id="fake", started_at=datetime.now(UTC), source="mock"
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
        lease_session = kwargs.get("lease_session")
        run = (
            lease_session.run  # type: ignore[attr-defined]
            if lease_session is not None
            else kwargs.get("run")
        )
        if run is None:
            run = PipelineRun(
                run_id="fake", started_at=datetime.now(UTC), source="evaluate"
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
            self,
            _sources: object,
            on_progress: object = None,
            run: object = None,  # noqa: ARG002
            lease_session: object | None = None,
        ) -> PipelineRun:
            """Block until gate is set, then call on_progress."""
            await gate.wait()
            r = (
                lease_session.run  # type: ignore[attr-defined]
                if lease_session is not None
                else PipelineRun(
                    run_id="x", started_at=datetime.now(UTC), source="mock"
                )
            )
            if on_progress is not None:
                on_progress(r)  # type: ignore[operator]
            return r

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
        rid == run_id and status == "succeeded" for rid, status in store.status_updates
    )
    assert store.lease_calls == ["start", "finalize"]
    assert store.legacy_calls == []


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
async def test_web_evaluate_dry_run_never_writes_lease_or_pipeline_row() -> None:
    """RunManager keeps preview-only evaluate work entirely in memory."""
    store = RecordingStore()
    mgr = _build_manager(store=store)

    await mgr.trigger_evaluate(stage="both", dry_run=True)
    await asyncio.sleep(0.05)

    assert store.lease_calls == []
    assert store.legacy_calls == []
    assert store._runs == []


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
        rid == run_id and status == "failed" for rid, status in store.status_updates
    )
    assert any(ev == "run_failed" for ev, _ in logger.events)
    assert mgr.get_active_runs() == []


@pytest.mark.asyncio
async def test_stale_owner_never_falls_back_to_legacy_finalize() -> None:
    """A rejected fence cannot be bypassed by the old status-update methods."""
    store = RecordingStore()
    store.finalize_result = False
    logger = RecordingLogger()
    mgr = _build_manager(store=store, logger=logger)

    await mgr.trigger_scan([("mock", object(), {})])
    await asyncio.sleep(0.1)

    assert store.lease_calls == ["start", "finalize"]
    assert store.legacy_calls == []
    assert store.status_updates == []
    assert any(ev == "run_failed" for ev, _ in logger.events)


@pytest.mark.asyncio
async def test_scan_factory_failure_releases_lock() -> None:
    """If scan_service_factory raises, the lock frees and nothing stays active."""

    def _failing_factory() -> object:
        msg = "factory exploded"
        raise RuntimeError(msg)

    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=_failing_factory,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )

    with pytest.raises(RuntimeError, match="factory exploded"):
        await mgr.trigger_scan([("mock", object(), {})])

    # Lock should be released, so a second trigger should NOT raise conflict
    assert not mgr._scan_lock.locked()
    assert mgr.get_active_runs() == []


@pytest.mark.asyncio
async def test_evaluate_factory_failure_releases_lock() -> None:
    """If evaluate_service_factory raises, the lock frees and no phantom run stays."""

    def _failing_factory(**_kw: object) -> object:
        msg = "factory exploded"
        raise RuntimeError(msg)

    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=FakeScanService,
        evaluate_service_factory=_failing_factory,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="factory exploded"):
        await mgr.trigger_evaluate(stage="both")

    # Lock should be released, so a second trigger should NOT raise conflict
    assert not mgr._eval_lock.locked()
    assert mgr.get_active_runs() == []


@pytest.mark.asyncio
async def test_scan_resolver_failure_fenced_finalizes_started_run() -> None:
    """Resolver store work happens after atomic start and failures are fenced."""

    async def _failing_resolver(
        _name: str, _stack: contextlib.AsyncExitStack
    ) -> list[SourceSpec]:
        raise SourceConfigError("Source 'speedyapply' is disabled")

    store = RecordingStore()
    mgr = RunManager(
        store=store,
        logger=RecordingLogger(),
        scan_service_factory=FakeScanService,
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
        scan_source_resolver=_failing_resolver,
    )

    with pytest.raises(SourceConfigError):
        await mgr.trigger_scan("speedyapply")

    assert not mgr._scan_lock.locked()
    assert mgr.get_active_runs() == []
    assert len(store._runs) == 1
    assert store.status_updates == [(store._runs[0].run_id, "failed")]
    assert store.lease_calls == ["start", "finalize"]
    assert store.legacy_calls == []


@pytest.mark.asyncio
async def test_scan_run_persists_failure_row_when_service_never_recorded() -> None:
    """A run whose service died before recording still lands as failed history."""
    store = RecordingStore()
    failing = FakeScanService(should_fail=True)
    mgr = _build_manager(store=store, scan_service=failing)
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    await asyncio.sleep(0.1)

    # FakeScanService raises before record_pipeline_run: _finish_run must
    # insert the row itself so the failed run does not vanish from history.
    persisted = await store.get_pipeline_run(run_id)
    assert persisted is not None
    assert persisted.status == "failed"


@pytest.mark.asyncio
async def test_finish_run_keeps_service_finalized_status() -> None:
    """A terminal status the service already set survives _finish_run."""

    class _NuancedScan:
        async def run(
            self,
            _sources: object,
            on_progress: object = None,  # noqa: ARG002
            run: PipelineRun | None = None,
            lease_session: object | None = None,
        ) -> PipelineRun:
            """Finalize with a nuanced terminal status, then return cleanly."""
            if lease_session is not None:
                run = lease_session.run  # type: ignore[attr-defined]
            assert run is not None
            run.status = "completed_with_errors"
            return run

    store = RecordingStore()
    mgr = RunManager(
        store=store,
        logger=RecordingLogger(),
        scan_service_factory=_NuancedScan,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    await asyncio.sleep(0.1)

    persisted = await store.get_pipeline_run(run_id)
    assert persisted is not None
    assert persisted.status == "completed_with_errors"  # not clobbered


@pytest.mark.asyncio
async def test_web_scan_preserves_requested_source_label() -> None:
    """A string-triggered scan keeps the requested source token as the label."""

    async def _resolver(
        _name: str, _stack: contextlib.AsyncExitStack
    ) -> list[SourceSpec]:
        return [("ats", object(), {})]

    gate = asyncio.Event()
    blocking = _make_gated_scan(gate)
    store = RecordingStore()
    mgr = RunManager(
        store=store,
        logger=RecordingLogger(),
        scan_service_factory=lambda: blocking,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
        scan_source_resolver=_resolver,
    )
    await mgr.trigger_scan("ats")

    active = mgr.get_active_runs()
    assert active[0].source == "ats"
    assert active[0].run.source == "ats"

    gate.set()
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Subscribe / broadcast tests
# ---------------------------------------------------------------------------


async def _drain(queue: asyncio.Queue[PipelineRun | object]) -> list[PipelineRun]:
    """Collect queue items until RUN_DONE arrives."""
    items: list[PipelineRun] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1)
        if item is RUN_DONE:
            return items
        items.append(item)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_subscribe_yields_progress_events() -> None:
    """subscribe queues should receive progress broadcasts then RUN_DONE."""
    gate = asyncio.Event()
    blocking = _make_gated_scan(gate)
    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=lambda: blocking,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    run_id = await mgr.trigger_scan([("mock", object(), {})])

    queue = mgr.subscribe(run_id)
    gate.set()
    received = await _drain(queue)
    mgr.unsubscribe(run_id, queue)

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

    queue_a = mgr.subscribe(run_id)
    queue_b = mgr.subscribe(run_id)
    gate.set()
    received_a, received_b = await asyncio.gather(_drain(queue_a), _drain(queue_b))

    assert len(received_a) == len(received_b)
    assert len(received_a) >= 1


@pytest.mark.asyncio
async def test_subscribe_after_finish_gets_immediate_done() -> None:
    """Subscribing to a finished run yields RUN_DONE without blocking."""
    mgr = _build_manager()
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    await asyncio.sleep(0.1)  # let the run finish

    queue = mgr.subscribe(run_id)
    item = await asyncio.wait_for(queue.get(), timeout=1)
    mgr.unsubscribe(run_id, queue)

    assert item is RUN_DONE


@pytest.mark.asyncio
async def test_broadcast_snapshots_are_immutable() -> None:
    """Queued progress events keep their counters despite later mutations."""

    class _TwoProgress:
        async def run(
            self,
            _sources: object,
            on_progress: object = None,
            run: PipelineRun | None = None,
            lease_session: object | None = None,
        ) -> PipelineRun:
            """Mutate the shared run between two progress broadcasts."""
            if lease_session is not None:
                run = lease_session.run  # type: ignore[attr-defined]
            assert run is not None
            run.jobs_discovered = 1
            on_progress(run)  # type: ignore[operator]
            run.jobs_discovered = 2
            on_progress(run)  # type: ignore[operator]
            return run

    mgr = RunManager(
        store=RecordingStore(),
        logger=RecordingLogger(),
        scan_service_factory=_TwoProgress,  # type: ignore[arg-type]
        evaluate_service_factory=lambda **_kw: FakeEvaluateService(),
    )
    run_id = await mgr.trigger_scan([("mock", object(), {})])
    queue = mgr.subscribe(run_id)

    received = await _drain(queue)
    mgr.unsubscribe(run_id, queue)

    # Without per-event snapshots both queued items would read 2.
    assert [r.jobs_discovered for r in received[:2]] == [1, 2]


# ---------------------------------------------------------------------------
# recover_stale_runs tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_stale_runs_leaves_recovery_to_store_connect() -> None:
    """RunManager must not scan or overwrite unexpired running rows."""
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

    assert recovered == 0
    assert store.status_updates == []
    assert stale_run.status == "running"
    assert store.legacy_calls == []


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
