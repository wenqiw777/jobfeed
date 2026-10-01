"""Historical identity work shares scan ownership without enrichment hooks."""

import asyncio

import pytest

from jobfeed.domain.errors import RunConflictError
from tests.unit.test_run_manager import _build_manager


async def test_backfill_blocks_other_scans_and_stop_releases_ownership():
    manager = _build_manager()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work(session, progress):
        assert session.kind == "scan"
        progress(session.run)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    run_id = await manager.trigger_application_backfill(work)
    await asyncio.wait_for(started.wait(), 1)
    assert manager.get_active_runs()[0].source == "application-backfill"
    with pytest.raises(RunConflictError):
        await manager.trigger_scan([])
    assert await manager.stop_run(run_id)
    assert cancelled.is_set()
    assert manager.get_active_runs() == []
    assert not manager._scan_lock.locked()


async def test_backfill_success_does_not_run_post_scan_enrichment():
    manager = _build_manager()

    async def forbidden(*_args):
        raise AssertionError("backfill must not run scan hooks")

    manager._post_scan_hook = forbidden

    async def work(session, progress):
        session.run.jobs_updated = 1
        progress(session.run)

    run_id = await manager.trigger_application_backfill(work)
    task = manager._tasks[run_id]
    await task
    assert manager._store._runs[0].status == "succeeded"
    assert manager.get_active_runs() == []
