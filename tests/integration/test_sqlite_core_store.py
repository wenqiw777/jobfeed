"""Integrated Task 2 contracts for the composed SQLite core store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting
from jobfeed.domain.models_views import JobsViewQuery
from jobfeed.services.run_orchestration import RunLeaseOrchestrator

_NOW = datetime(2026, 8, 12, 20, 30, tzinfo=UTC)


async def test_core_store_composes_jobs_claims_and_fenced_runs(tmp_path: Path) -> None:
    """One store owns lifecycle, application time, claims, and run hydration."""
    store = SQLiteStore(tmp_path / "jobfeed.db", clock=lambda: _NOW)
    await store.connect()

    saved = await store.save_job(
        JobPosting(
            platform="test",
            canonical_id="job-1",
            url="https://example.com/1",
            title="Engineer",
            company="Example",
            location="Remote",
            discovered_at=_NOW - timedelta(minutes=5),
            jd_text="complete description",
        )
    )
    claimed = await store.claim_pending_stage_a(limit=1)
    assert [job.id for job in claimed] == [saved.job_id]

    orchestrator = RunLeaseOrchestrator(store, clock=lambda: _NOW)

    async def work(session: object) -> None:
        del session

    run = await orchestrator.run("scan", "test", work)
    hydrated = await store.get_pipeline_run(run.run_id)
    assert hydrated == run

    await store.close()
    with pytest.raises(RuntimeError, match="not open"):
        await store.list_jobs()


async def test_connect_recovers_only_expired_run_lease(tmp_path: Path) -> None:
    """Facade startup performs the lifecycle's expired-only recovery once."""
    path = tmp_path / "jobfeed.db"
    old_now = _NOW - timedelta(minutes=10)
    first = SQLiteStore(path, clock=lambda: old_now)
    await first.connect()
    session = await RunLeaseOrchestrator(first, clock=lambda: old_now).start(
        "evaluate", "evaluate"
    )
    run_id = session.run.run_id
    await session._stop_heartbeat()
    await first.close()

    second = SQLiteStore(path, clock=lambda: _NOW)
    await second.connect()
    recovered = await second.get_pipeline_run(run_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.finished_at == _NOW
    await second.close()


async def test_store_composes_task_three_capabilities(tmp_path: Path) -> None:
    """One facade exposes status, ops, views, and performance capabilities."""
    store = SQLiteStore(tmp_path / "jobfeed.db", clock=lambda: _NOW)
    await store.connect()
    saved = await store.save_job(
        JobPosting(
            platform="test",
            canonical_id="task-3",
            url="https://example.com/task-3",
            title="Engineer",
            company="Example",
            location="Remote",
            discovered_at=_NOW,
            jd_text="complete description",
        )
    )

    await store.set_state("task3", "composed")
    assert await store.get_state("task3") == "composed"
    status = await store.get_status(saved.job_id)
    assert status is not None
    assert status.status == "new"
    page = await store.query_jobs_view(JobsViewQuery(tab="all"))
    assert [row.job.id for row in page.rows] == [saved.job_id]
    assert await store.get_step_timings(30) == []

    await store.close()
