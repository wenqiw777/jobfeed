"""Explicit Stop/shutdown persist while a real priority finalizer is committing."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import PipelineRun
from tests.unit.test_run_manager import _build_manager


@pytest.mark.parametrize("reason", ["user_stopped", "interrupted"])
@pytest.mark.parametrize("window", ["after_update", "after_commit"])
async def test_explicit_stop_overrides_already_bound_sqlite_priority_update(
    tmp_path, reason, window
):
    entered, finalizing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class PausedFinalizeStore(SQLiteStore):
        async def _after_finalize_run_update(self, _connection):
            if window == "after_update" and not finalizing.is_set():
                finalizing.set()
                await release.wait()

        async def finalize_run_with_lease(self, run, **kwargs):
            result = await super().finalize_run_with_lease(run, **kwargs)
            if window == "after_commit" and run.source == "application-backfill":
                finalizing.set()
                await release.wait()
            return result

    store = PausedFinalizeStore(tmp_path / "late-stop.sqlite")
    await store.connect()
    manager = _build_manager(store=store)

    async def work(_session, _progress):
        entered.set()
        await asyncio.Event().wait()

    foreground = explicit = None
    try:
        old = await manager.trigger_application_backfill(work)
        await entered.wait()
        foreground = asyncio.create_task(manager.trigger_scan([]))
        await asyncio.wait_for(finalizing.wait(), 1)
        explicit = asyncio.create_task(
            manager.stop_run(old) if reason == "user_stopped" else manager.shutdown()
        )
        await asyncio.sleep(0)
        assert manager._active[old].run.failure_code == reason
        release.set()
        result = await asyncio.wait_for(explicit, 1)
        assert result is True if reason == "user_stopped" else result is None
        outcome = (await asyncio.gather(foreground, return_exceptions=True))[0]
        if reason == "interrupted":
            assert isinstance(outcome, RunConflictError)
        else:
            task = manager._tasks.get(outcome)
            if task is not None:
                await task
        durable = await store.get_pipeline_run(old)
        assert durable.failure_code == reason
        assert durable.status == "failed"
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute("SELECT owner_id,run_id FROM run_leases")
            assert all(tuple(row) == (None, None) for row in await cursor.fetchall())
            await cursor.close()
    finally:
        release.set()
        tasks = [task for task in (foreground, explicit) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await manager.shutdown()
        await store.close()


async def test_late_manual_stop_remains_user_stopped_when_shutdown_follows(tmp_path):
    entered, finalizing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class PausedFinalizeStore(SQLiteStore):
        async def _after_finalize_run_update(self, _connection):
            finalizing.set()
            await release.wait()

    store = PausedFinalizeStore(tmp_path / "manual-shutdown.sqlite")
    await store.connect()
    manager = _build_manager(store=store)

    async def work(_session, _progress):
        entered.set()
        await asyncio.Event().wait()

    tasks = []
    try:
        old = await manager.trigger_application_backfill(work)
        await entered.wait()
        foreground = asyncio.create_task(manager.trigger_scan([]))
        tasks.append(foreground)
        await asyncio.wait_for(finalizing.wait(), 1)
        stop = asyncio.create_task(manager.stop_run(old))
        tasks.append(stop)
        await asyncio.sleep(0)
        shutdown = asyncio.create_task(manager.shutdown())
        tasks.append(shutdown)
        await asyncio.sleep(0)
        assert manager._active[old].run.failure_code == "user_stopped"
        release.set()
        assert await asyncio.wait_for(stop, 1)
        await asyncio.wait_for(shutdown, 1)
        with pytest.raises(RunConflictError):
            await foreground
        assert (await store.get_pipeline_run(old)).failure_code == "user_stopped"
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await manager.shutdown()
        await store.close()


@pytest.mark.parametrize(
    "source,status,code,changed",
    [
        ("application-backfill", "failed", "foreground_priority", True),
        ("application-backfill", "running", "foreground_priority", False),
        ("application-backfill", "succeeded", "foreground_priority", False),
        ("application-backfill", "failed", "user_stopped", False),
        ("application-backfill", "failed", "interrupted", True),
        ("ordinary", "failed", "foreground_priority", False),
    ],
)
async def test_reason_correction_changes_only_priority_terminal_and_never_lease(
    tmp_path, source, status, code, changed
):
    store = SQLiteStore(tmp_path / "conditional-stop.sqlite")
    await store.connect()
    now = datetime.now(UTC)
    row = PipelineRun(
        run_id=str(uuid4()),
        source=source,
        started_at=now,
    )
    lease = PipelineRun(run_id=str(uuid4()), source="ordinary", started_at=now)
    try:
        assert await store.start_run_with_lease(
            row, kind="evaluate", owner_id=str(uuid4()), now=now
        )
        async with store._lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE pipeline_runs SET status=?,failure_code=? WHERE run_id=?",
                (status, code, row.run_id),
            )
            await connection.commit()
        assert await store.start_run_with_lease(
            lease, kind="scan", owner_id=str(uuid4()), now=now
        )
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute("SELECT * FROM run_leases ORDER BY kind")
            before = [tuple(value) for value in await cursor.fetchall()]
            await cursor.close()
        assert (
            await store.override_application_backfill_stop(
                row.run_id, failure_code="user_stopped"
            )
            is changed
        )
        durable = await store.get_pipeline_run(row.run_id)
        assert durable.failure_code == ("user_stopped" if changed else code)
        assert durable.status == status
        assert not await store.override_application_backfill_stop(
            row.run_id, failure_code="interrupted"
        )
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute("SELECT * FROM run_leases ORDER BY kind")
            assert [tuple(value) for value in await cursor.fetchall()] == before
            await cursor.close()
    finally:
        await store.close()


async def test_user_stop_during_shutdown_correction_write_wins(tmp_path):
    entered, finalizing = asyncio.Event(), asyncio.Event()
    release_finalize, correcting, release_correction = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    class PausedCorrectionStore(SQLiteStore):
        async def _after_finalize_run_update(self, _connection):
            finalizing.set()
            await release_finalize.wait()

        async def override_application_backfill_stop(self, run_id, *, failure_code):
            result = await super().override_application_backfill_stop(
                run_id, failure_code=failure_code
            )
            if failure_code == "interrupted":
                correcting.set()
                await release_correction.wait()
            return result

    store = PausedCorrectionStore(tmp_path / "correction-stop.sqlite")
    await store.connect()
    manager = _build_manager(store=store)

    async def work(_session, _progress):
        entered.set()
        await asyncio.Event().wait()

    tasks = []
    try:
        old = await manager.trigger_application_backfill(work)
        await entered.wait()
        foreground = asyncio.create_task(manager.trigger_scan([]))
        tasks.append(foreground)
        await asyncio.wait_for(finalizing.wait(), 1)
        shutdown = asyncio.create_task(manager.shutdown())
        tasks.append(shutdown)
        await asyncio.sleep(0)
        release_finalize.set()
        await asyncio.wait_for(correcting.wait(), 1)
        assert manager._scan_lock.locked()
        stop = asyncio.create_task(manager.stop_run(old))
        tasks.append(stop)
        await asyncio.sleep(0)
        assert manager._active[old].run.failure_code == "user_stopped"
        release_correction.set()
        assert await asyncio.wait_for(stop, 1)
        await asyncio.wait_for(shutdown, 1)
        with pytest.raises(RunConflictError):
            await foreground
        assert (await store.get_pipeline_run(old)).failure_code == "user_stopped"
    finally:
        release_finalize.set()
        release_correction.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await manager.shutdown()
        await store.close()
