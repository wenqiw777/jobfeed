"""Whole-run proof: four sources enter before completion and persist progress."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.ports.source import PartialSourceFetchError
from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.services.scan import ScanService


async def test_warning_keeps_saved_rows_without_error_counter(tmp_path):
    store = SQLiteStore(tmp_path / "warning.sqlite")
    await store.connect()

    class Source:
        async def fetch_jobs(self, _config):
            raise PartialSourceFetchError(
                "Unconfirmed empty page at offset 25", [], warning=True
            )

    try:
        run = await ScanService(store, MagicMock()).run([("linkedin", Source(), {})])
        saved = await store.get_pipeline_run(run.run_id)
        assert saved.status == "succeeded"
        assert saved.errors == 0
        assert saved.scan_progress["linkedin"]["phase"] == "completed_with_warnings"
        assert "offset 25" in saved.scan_progress["linkedin"]["message"]
    finally:
        await store.close()


async def test_parallel_run_retains_all_four_source_progress_rows(tmp_path):
    store = SQLiteStore(tmp_path / "parallel.sqlite")
    await store.connect()
    bridge = JobrightBridge()
    names = ["jobright", "linkedin", "handshake", "github-jd"]
    connection = bridge.connect(names)

    class Source:
        def __init__(self, name):
            self.name = name

        async def fetch_jobs(self, config):
            return await self.fetch_jobs_with_progress(config, lambda _: None)

        async def fetch_jobs_with_progress(self, _config, on_progress):
            await bridge.run_scan(
                source=self.name,
                max_jobs=1,
                batch_size=1,
                pacing_s=0,
                timeout_s=5,
                on_progress=on_progress,
            )
            return [
                JobPosting(
                    platform=self.name,
                    canonical_id="one",
                    company="Example",
                    title="SWE",
                    location="NY",
                    discovered_at=datetime.now(UTC),
                    url=f"https://example.com/{self.name}",
                    jd_text="Saved body",
                    jd_quality=QualityBand.GOOD,
                )
            ]

    async def respond():
        commands = [await asyncio.wait_for(connection.next_command(), 1) for _ in names]
        for command in reversed(commands):
            await bridge.receive({"type": "complete", "task_id": command["task_id"]})

    response = asyncio.create_task(respond())
    try:
        run = await ScanService(store, MagicMock()).run(
            [(name, Source(name), {}) for name in names]
        )
        await response
        assert run.jobs_inserted == len(names)
        saved = await store.get_pipeline_run(run.run_id)
        assert set(saved.scan_progress) == set(names)
        assert all(
            progress["phase"] == "completed"
            for progress in saved.scan_progress.values()
        )
    finally:
        response.cancel()
        await asyncio.gather(response, return_exceptions=True)
        await store.close()
