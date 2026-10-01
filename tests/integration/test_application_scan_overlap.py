"""Actual source commits and canonical merges while later batches are fetched."""

import asyncio
from datetime import UTC, datetime

import pytest
import structlog

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.application_route import ApplicationRouteOutcome
from jobfeed.domain.models import JobPosting
from jobfeed.services.scan import ScanService

ATS = "https://acme.wd5.myworkdayjobs.com/external/job/Boston/Engineer_R123"
SOURCE_COUNT = 2


def posting(key):
    return JobPosting(
        platform="linkedin",
        canonical_id=key,
        url=f"https://www.linkedin.com/jobs/view/{key}/",
        title="Engineer",
        company="Acme",
        location="Boston",
        discovered_at=datetime.now(UTC),
        jd_text="Original full job description",
        apply_url=f"https://careers.example.test/jobs/{key}",
    )


@pytest.mark.parametrize("reverse", [False, True])
async def test_streaming_scan_advances_during_resolution_and_drains_before_return(
    tmp_path, reverse
):
    store = SQLiteStore(tmp_path / "overlap.db")
    await store.connect()
    started = {key: asyncio.Event() for key in ("1", "2")}
    release = {key: asyncio.Event() for key in ("1", "2")}
    second_batch = asyncio.Event()

    class Source:
        async def fetch_jobs(self, _config):
            raise AssertionError("Streaming source must not buffer until completion")

        async def fetch_jobs_streaming(self, _config, _on_progress, on_batch):
            await on_batch([posting("1")])
            await asyncio.wait_for(started["1"].wait(), 2)
            await on_batch([posting("2")])
            second_batch.set()
            return [posting("1"), posting("2")]

    async def resolve(job):
        started[job.canonical_id].set()
        await release[job.canonical_id].wait()
        return ApplicationRouteOutcome(status="resolved", ats_url=ATS)

    scan = asyncio.create_task(
        ScanService(
            store, structlog.get_logger("test"), application_resolver=resolve
        ).run([("linkedin", Source(), {})])
    )
    try:
        await asyncio.wait_for(second_batch.wait(), 2)
        await asyncio.wait_for(started["2"].wait(), 2)
        assert not scan.done()
        first, second = ("2", "1") if reverse else ("1", "2")
        release[first].set()
        await asyncio.sleep(0)
        release[second].set()
        run = await asyncio.wait_for(scan, 5)
        jobs = await store.list_jobs()
        assert len(jobs) == SOURCE_COUNT
        assert run.jobs_inserted == SOURCE_COUNT and run.jobs_discovered == SOURCE_COUNT
        async with store._lifecycle.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT canonical_id,real_job_id,apply_url,jd_text "
                    "FROM jobs ORDER BY id"
                )
            ).fetchall()
            assert len({row[1] for row in rows}) == 1
            assert all(
                row[2].startswith("https://careers.example.test/") for row in rows
            )
            assert all(row[3] == "Original full job description" for row in rows)
            assert (
                await (
                    await connection.execute(
                        "SELECT COUNT(*) FROM real_job_identifiers "
                        "WHERE provider='workday' AND scope='acme' AND native_id='R123'"
                    )
                ).fetchone()
            )[0] == 1
        assert run.scan_progress["application-resolution"]["phase"] == "completed"
    finally:
        scan.cancel()
        await asyncio.gather(scan, return_exceptions=True)
        await store.close()


async def test_next_browser_batch_arrives_while_previous_database_save_waits(tmp_path):
    saving, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class DelayedStore(SQLiteStore):
        async def save_job(self, job):
            if job.canonical_id == "1":
                saving.set()
                await release.wait()
            return await super().save_job(job)

    class Source:
        async def fetch_jobs(self, _config):
            raise AssertionError("streaming callback required")

        async def fetch_jobs_streaming(self, _config, _on_progress, on_batch):
            await on_batch([posting("1")])
            await saving.wait()
            await on_batch([posting("2")])
            delivered.set()
            return [posting("1"), posting("2")]

    store = DelayedStore(tmp_path / "independent-writer.db")
    await store.connect()
    scan = asyncio.create_task(
        ScanService(store, structlog.get_logger("test")).run(
            [("linkedin", Source(), {})]
        )
    )
    try:
        await asyncio.wait_for(delivered.wait(), 2)
        assert not scan.done()
        assert await store.list_jobs() == []
        release.set()
        run = await asyncio.wait_for(scan, 5)
        assert run.jobs_inserted == SOURCE_COUNT and run.jobs_updated == 0
        assert len(await store.list_jobs()) == SOURCE_COUNT
    finally:
        scan.cancel()
        await asyncio.gather(scan, return_exceptions=True)
        await store.close()
