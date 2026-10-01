"""Foreground priority releases real SQLite fences and keeps committed evidence."""

import asyncio
from dataclasses import replace

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.application_route import ApplicationRouteOutcome
from jobfeed.services.application_backfill import ApplicationBackfillService
from tests.support.sqlite_jobs_evaluations import make_job
from tests.unit.test_run_manager import _build_manager


@pytest.mark.parametrize("kind", ["scan", "evaluate"])
async def test_priority_preserves_commits_and_releases_sqlite_fence(tmp_path, kind):
    store = SQLiteStore(tmp_path / "priority.sqlite")
    await store.connect()
    manager = _build_manager(store=store)
    committed, pending, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    late = asyncio.Event()
    ids = []
    for key in ("committed", "pending"):
        ids.append(
            (await store.save_job(replace(make_job(key), platform="linkedin"))).job_id
        )
    before = [await store.get_job(key) for key in ids]
    await store.save_hard_filters({ids[0]: "historical filter"})
    async with store._lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT job_id,stage_a_status,stage_a_score,stage_a_one_line,"
            "stage_b_status,stage_b_verdict FROM evaluations ORDER BY job_id"
        )
        evaluations = [tuple(row) for row in await cursor.fetchall()]
        await cursor.close()

    async def resolve(job):
        if job.id == ids[1]:
            pending.set()
            try:
                await late.wait()
            finally:
                cancelled.set()
        return ApplicationRouteOutcome(
            "resolved", ats_url="https://boards.greenhouse.io/example/jobs/123456"
        )

    async def work(session, progress):
        def publish(run):
            progress(run)
            if run.scan_processed == 1:
                committed.set()

        await ApplicationBackfillService(store, resolve).run(
            ids, lease_session=session, on_progress=publish
        )

    try:
        old_id = await manager.trigger_application_backfill(work)
        await asyncio.wait_for(committed.wait(), 1)
        await asyncio.wait_for(pending.wait(), 1)
        assert await store.get_state("application-resolution:linkedin:committed")
        if kind == "scan":
            new_id = await manager.trigger_scan([])
        else:
            new_id = await manager.trigger_evaluate(scope="backlog")
        task = manager._tasks.get(new_id)
        if task is not None:
            await task
        assert cancelled.is_set()
        old = await store.get_pipeline_run(old_id)
        new = await store.get_pipeline_run(new_id)
        assert old.failure_code == "foreground_priority"
        assert new.status == "succeeded"
        assert old.finished_at <= new.started_at
        late.set()
        await asyncio.sleep(0)
        assert await store.get_state("application-resolution:linkedin:pending") is None
        assert [await store.get_job(key) for key in ids] == before
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT job_id,stage_a_status,stage_a_score,stage_a_one_line,"
                "stage_b_status,stage_b_verdict FROM evaluations ORDER BY job_id"
            )
            assert [tuple(row) for row in await cursor.fetchall()] == evaluations
            await cursor.close()
            cursor = await connection.execute("SELECT owner_id,run_id FROM run_leases")
            assert all(tuple(row) == (None, None) for row in await cursor.fetchall())
            await cursor.close()
    finally:
        late.set()
        await manager.shutdown()
        await store.close()
