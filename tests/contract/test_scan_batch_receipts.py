"""Database commit receipts make Redis redelivery safe for jobs and counters."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from jobfeed.adapters.store import _sqlite_jobs
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, PipelineRun


async def test_batch_replay_preserves_original_insert_outcomes(tmp_path):
    store = SQLiteStore(tmp_path / "receipts.sqlite")
    await store.connect()
    now = datetime.now(UTC)
    owner = str(uuid4())
    run = PipelineRun(run_id=str(uuid4()), source="all", started_at=now)
    generation = await store.start_run_with_lease(
        run, kind="scan", owner_id=owner, now=now
    )
    jobs = [
        JobPosting(
            platform="test",
            canonical_id=str(i),
            url=f"https://example.com/{i}",
            title="SWE",
            company="Example",
            location="US",
            discovered_at=now,
        )
        for i in range(3)
    ]
    try:
        first = await store.save_job_batch(
            jobs,
            receipt_key="pipeline:one:batch:one",
            run_id=run.run_id,
            owner_id=owner,
            generation=generation,
        )
        again = await store.save_job_batch(
            jobs,
            receipt_key="pipeline:one:batch:one",
            run_id=run.run_id,
            owner_id=owner,
            generation=generation,
        )
        assert first == again
        assert all(result.inserted for result in again)
        assert len(await store.list_jobs(limit=10)) == len(jobs)
        with pytest.raises(RuntimeError, match="lease"):
            await store.save_job_batch(
                jobs,
                receipt_key="pipeline:one:batch:two",
                run_id="run",
                owner_id="stale",
                generation=generation,
            )
    finally:
        await store.close()


async def test_batch_rollback_leaves_neither_jobs_nor_receipt(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "rollback.sqlite")
    await store.connect()
    now = datetime.now(UTC)
    owner = str(uuid4())
    run = PipelineRun(run_id=str(uuid4()), source="all", started_at=now)
    try:
        generation = await store.start_run_with_lease(
            run, kind="scan", owner_id=owner, now=now
        )
        original = _sqlite_jobs._save_job_on_connection

        async def fail_second(connection, job):
            if job.canonical_id == "two":
                raise RuntimeError("injected second upsert failure")
            return await original(connection, job)

        monkeypatch.setattr(_sqlite_jobs, "_save_job_on_connection", fail_second)
        jobs = [
            JobPosting(
                platform="test",
                canonical_id=key,
                title="SWE",
                company="ACME",
                location="US",
                url=f"https://example.com/{key}",
                discovered_at=now,
            )
            for key in ["one", "two"]
        ]
        with pytest.raises(RuntimeError, match="second upsert"):
            await store.save_job_batch(
                jobs,
                receipt_key="rollback-receipt",
                run_id=run.run_id,
                owner_id=owner,
                generation=generation,
            )
        assert await store.list_jobs() == []
        assert await store.get_state("rollback-receipt") is None
    finally:
        await store.close()
