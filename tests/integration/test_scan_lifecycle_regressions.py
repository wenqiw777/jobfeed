"""Run monitoring and shutdown must not compete with live scan writes."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import structlog

from jobfeed.adapters.store import _sqlite_real_job_evaluation as claims_module
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import QualityBand
from jobfeed.web.app import build_web_app
from tests.support.factories import make_job
from tests.support.sqlite_run_lease_fixtures import NOW
from tests.unit.test_run_manager import _build_manager, _make_gated_scan
from tests.unit.test_run_orchestration import LeaseStore, _orchestrator
from tests.web.test_app_skeleton import FakeStore, fake_context, open_client

SKIPPED_COUNT = 20
HTTP_OK = 200
RENEWALS = 2


async def test_idle_recovery_does_not_wait_for_a_writer(tmp_path: Path) -> None:
    lifecycle = SqliteLifecycle(tmp_path / "idle.db", ensure_sqlite_schema)
    await lifecycle.open()
    try:
        async with lifecycle.connection() as writer:
            await writer.execute("BEGIN IMMEDIATE")
            try:
                result = await asyncio.wait_for(
                    SqliteClaimsRuns(lifecycle).recover_expired_run_leases(now=NOW),
                    timeout=0.5,
                )
                assert result == []
            finally:
                await writer.rollback()
    finally:
        await lifecycle.close()


@pytest.mark.parametrize("stage", ["a", "b"])
async def test_skipped_candidates_release_writer_between_candidates(
    tmp_path: Path, monkeypatch, stage: str
) -> None:
    store = SQLiteStore(tmp_path / "claims.db")
    await store.connect()
    original = claims_module._immediate_transaction
    reads_per_transaction = []
    competing_writes = []
    active_reads = 0
    original_one = claims_module._one

    async def counted_one(*args, **kwargs):
        nonlocal active_reads
        active_reads += 1
        return await original_one(*args, **kwargs)

    @asynccontextmanager
    async def tracked(connection):
        nonlocal active_reads
        active_reads = 0
        async with original(connection):
            yield
        reads_per_transaction.append(active_reads)
        # Another connection can acquire the writer before the next candidate.
        async with store._lifecycle.connection() as writer, original(writer):
            await writer.execute("INSERT OR REPLACE INTO state(key,value) VALUES (?,?)",
                                 ("claim-test-writer", str(len(competing_writes))))
            competing_writes.append(True)

    monkeypatch.setattr(claims_module, "_one", counted_one)
    monkeypatch.setattr(claims_module, "_immediate_transaction", tracked)
    try:
        claim = getattr(store, f"claim_real_job_stage_{stage}_by_ids")
        kwargs = {"stage_a_threshold": 80} if stage == "b" else {}
        ids = [str(i) for i in range(1, SKIPPED_COUNT + 1)]
        assert await claim(ids, **kwargs) == []
        assert max(reads_per_transaction) == 1
        assert len(reads_per_transaction) == SKIPPED_COUNT
        assert len(competing_writes) == SKIPPED_COUNT
    finally:
        await store.close()


async def test_runs_get_does_not_trigger_recovery(monkeypatch) -> None:
    store = FakeStore()
    async def listing(**_kwargs):
        return [], 0
    monkeypatch.setattr(store, "list_pipeline_runs", listing, raising=False)
    app = build_web_app(fake_context(store))
    async with open_client(app) as client:
        async def forbidden():
            raise AssertionError("GET attempted write recovery")
        monkeypatch.setattr(app.state.run_manager, "recover_stale_runs", forbidden)
        assert (await client.get("/api/runs")).status_code == HTTP_OK
        assert (await client.get("/api/runs/active")).status_code == HTTP_OK


async def test_shutdown_drains_scan_and_rejects_new_work() -> None:
    manager = _build_manager(scan_service=_make_gated_scan(asyncio.Event()))
    run_id = await manager.trigger_scan([])
    task = manager._tasks[run_id]
    run = manager._active[run_id].run
    try:
        await manager.shutdown()
        assert task.done()
        assert not manager.get_active_runs()
        assert run.failure_code == "interrupted"
        assert "shutdown" in run.failure_message.lower()
        with pytest.raises(RunConflictError, match="shutting down"):
            await manager.trigger_scan([])
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_app_drains_manager_before_store_close(monkeypatch) -> None:
    events = []
    store = FakeStore()
    app = build_web_app(fake_context(store))
    async def shutdown():
        events.append("drained")
    async def close():
        events.append("closed")
    monkeypatch.setattr(app.state.run_manager, "shutdown", shutdown, raising=False)
    monkeypatch.setattr(store, "close", close)
    async with open_client(app):
        pass
    assert events == ["drained", "closed"]


async def test_heartbeat_reports_failure_and_fence_rejection() -> None:
    store = LeaseStore()
    store.renew_errors_remaining = 1
    orchestrator = _orchestrator(store, heartbeat_interval_seconds=0.001)
    with structlog.testing.capture_logs() as logs:
        session = await orchestrator.start("scan", "linkedin")
        while len([name for name, _ in store.calls if name == "renew"]) < RENEWALS:
            await asyncio.sleep(0)
        await session._stop_heartbeat()
    failure = [x for x in logs if x["event"] == "run_lease_renewal_error"]
    assert len(failure) == 1
    assert failure[0]["run_id"] == session.run.run_id
    assert failure[0]["error_type"] == "RuntimeError"
    store.renew_result = False
    with structlog.testing.capture_logs() as logs:
        assert await session._renew_with_transient_retry() is False
    assert any(x["event"] == "run_lease_fence_rejected" for x in logs)


@pytest.mark.parametrize("stage", ["a", "b"])
async def test_short_claims_keep_two_workers_exclusive(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "two-workers.db"
    first, second = SQLiteStore(path), SQLiteStore(path)
    await first.connect()
    await second.connect()
    try:
        saved = await first.save_job(make_job(
            platform="linkedin", jd_text="Build services and production APIs. " * 20,
            jd_quality=QualityBand.FULL,
        ))
        real_id = await first.resolve_real_job_id(saved.job_id)
        if stage == "b":
            await first.claim_real_job_stage_a_by_ids([real_id])
            async with first._lifecycle.connection() as connection:
                await connection.execute(
                    "UPDATE real_job_evaluations SET stage_a_status='completed',"
                    "stage_a_score=90 WHERE real_job_id=?", (int(real_id),)
                )
        kwargs = {"stage_a_threshold": 80} if stage == "b" else {}
        results = await asyncio.gather(*[
            getattr(worker, f"claim_real_job_stage_{stage}_by_ids")([real_id], **kwargs)
            for worker in (first, second)
        ])
        assert sum(map(len, results)) == 1
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("stage", ["a", "b"])
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failed_claim_batch_releases_only_unreturned_claims(
    tmp_path: Path, monkeypatch, stage: str, failure
) -> None:
    store = SQLiteStore(tmp_path / "interrupted-claims.db")
    await store.connect()
    try:
        saved = await store.save_job(make_job(
            platform="linkedin", jd_text="Build services and production APIs. " * 20,
            jd_quality=QualityBand.FULL,
        ))
        real_id = await store.resolve_real_job_id(saved.job_id)
        if stage == "b":
            await store.claim_real_job_stage_a_by_ids([real_id])
            async with store._lifecycle.connection() as connection:
                await connection.execute(
                    "UPDATE real_job_evaluations SET stage_a_status='completed',"
                    "stage_a_score=90 WHERE real_job_id=?", (int(real_id),)
                )
        original_one = claims_module._one
        async def fail_second(connection, sql, params):
            if params == (int(real_id) + 1,) and "identity_review_state" in sql:
                raise failure("second candidate failed")
            return await original_one(connection, sql, params)
        monkeypatch.setattr(claims_module, "_one", fail_second)
        claim = getattr(store, f"claim_real_job_stage_{stage}_by_ids")
        kwargs = {"stage_a_threshold": 80} if stage == "b" else {}
        with pytest.raises(failure):
            await claim([real_id, str(int(real_id) + 1)], **kwargs)
        assert len(await claim([real_id], **kwargs)) == 1
    finally:
        await store.close()


async def test_shutdown_waits_for_inflight_source_setup() -> None:
    entered, proceed = asyncio.Event(), asyncio.Event()
    manager = _build_manager(scan_service=_make_gated_scan(asyncio.Event()))
    async def resolver(_source, _stack):
        entered.set()
        await proceed.wait()
        return []
    manager._source_resolver = resolver
    trigger = asyncio.create_task(manager.trigger_scan("linkedin"))
    await entered.wait()
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    proceed.set()
    run_id = await trigger
    await asyncio.wait_for(shutdown, timeout=1)
    assert not manager.get_active_runs()
    run = await manager._store.get_pipeline_run(run_id)
    assert run.failure_code == "interrupted"


async def test_cancelled_source_setup_releases_lease_and_resources() -> None:
    entered, cleaned = asyncio.Event(), asyncio.Event()
    manager = _build_manager()
    @asynccontextmanager
    async def resource():
        try:
            yield
        finally:
            cleaned.set()
    async def resolver(_source, stack):
        await stack.enter_async_context(resource())
        entered.set()
        await asyncio.Event().wait()
    manager._source_resolver = resolver
    trigger = asyncio.create_task(manager.trigger_scan("linkedin"))
    await entered.wait()
    trigger.cancel("service_shutdown")
    await asyncio.gather(trigger, return_exceptions=True)
    assert cleaned.is_set()
    assert not manager._scan_lock.locked()
    assert not manager.get_active_runs()
    assert manager._store._runs[0].failure_code == "interrupted"
    assert manager._store.lease_calls[-1] == "finalize"
