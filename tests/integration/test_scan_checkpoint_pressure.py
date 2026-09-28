"""Real SQLite survives thousands of concurrent source-progress notifications."""

import asyncio
from unittest.mock import MagicMock

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.services.run_manager import RunManager


async def test_four_lane_progress_burst_persists_terminal_snapshot(tmp_path):
    store = SQLiteStore(tmp_path / "pressure.sqlite")
    await store.connect()
    peak_pending = 0

    class BurstScan:
        async def run(self, _sources, **kwargs):
            run = kwargs["lease_session"].run

            async def lane(name):
                nonlocal peak_pending
                for processed in range(1000):
                    run.scan_progress[name] = {
                        "phase": "fetching",
                        "processed": processed,
                    }
                    kwargs["on_progress"](run)
                    peak_pending = max(
                        peak_pending, len(manager._checkpoint_tasks.get(run.run_id, ()))
                    )
                    if processed % 10 == 0:
                        await asyncio.sleep(0)
                run.scan_progress[name] = {"phase": "completed", "processed": 1000}
                kwargs["on_progress"](run)

            await asyncio.gather(
                *(
                    lane(name)
                    for name in ("jobright", "linkedin", "handshake", "speedyapply")
                )
            )
            return run

    manager = RunManager(
        store=store,
        logger=MagicMock(),
        scan_service_factory=BurstScan,
        evaluate_service_factory=MagicMock(),
    )
    try:
        run_id = await manager.trigger_scan([("all", object(), {})])
        await asyncio.wait_for(manager._tasks[run_id], timeout=10)
        saved = await store.get_pipeline_run(run_id)
        assert saved.status == "succeeded"
        assert set(saved.scan_progress) == {
            "jobright",
            "linkedin",
            "handshake",
            "speedyapply",
        }
        assert all(
            p == {"phase": "completed", "processed": 1000}
            for p in saved.scan_progress.values()
        )
        assert peak_pending == 1
        assert not manager._checkpoint_tasks
        assert not manager._scan_checkpoint_flush
    finally:
        await store.close()
