"""Step timings and pipeline run status integration tests against PostgreSQL.

Covers record_step_timing round-trip, record_step_timings batch insert,
PipelineRun.status persistence, and update_pipeline_run_status transitions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.models_perf import StepTiming

pytestmark = pytest.mark.postgres

FIXED_TIME = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


def _make_run(run_id: str = "run-timing-1", status: str = "running") -> PipelineRun:
    """Build a minimal PipelineRun fixture.

    Args:
        run_id: Pipeline run identity.
        status: Initial run status.

    Returns:
        Pipeline run fixture.
    """
    return PipelineRun(
        run_id=run_id, started_at=FIXED_TIME, source="test", status=status
    )


async def test_pipeline_run_status_persists(store: PostgresStore) -> None:
    """PipelineRun.status round-trips through record + get."""
    run = _make_run("run-status-1", status="running")
    await store.record_pipeline_run(run)

    loaded = await store.get_pipeline_run("run-status-1")

    assert loaded is not None
    assert loaded.status == "running"


async def test_pipeline_run_status_default_running(store: PostgresStore) -> None:
    """A run recorded with default status uses 'running'."""
    run = PipelineRun(run_id="run-default-1", started_at=FIXED_TIME, source="test")
    await store.record_pipeline_run(run)

    loaded = await store.get_pipeline_run("run-default-1")

    assert loaded is not None
    assert loaded.status == "running"


async def test_update_pipeline_run_status(store: PostgresStore) -> None:
    """update_pipeline_run_status transitions status and sets finished_at."""
    run = _make_run("run-update-1")
    await store.record_pipeline_run(run)

    run.status = "succeeded"
    run.finished_at = datetime(2026, 6, 16, 12, 30, tzinfo=UTC)
    await store.update_pipeline_run_status(run)

    loaded = await store.get_pipeline_run("run-update-1")
    assert loaded is not None
    assert loaded.status == "succeeded"
    assert loaded.finished_at is not None


async def test_update_pipeline_run_status_failed(store: PostgresStore) -> None:
    """update_pipeline_run_status can record a failure."""
    run = _make_run("run-fail-1")
    await store.record_pipeline_run(run)

    run.status = "failed"
    run.finished_at = datetime(2026, 6, 16, 12, 15, tzinfo=UTC)
    await store.update_pipeline_run_status(run)

    loaded = await store.get_pipeline_run("run-fail-1")
    assert loaded is not None
    assert loaded.status == "failed"


async def test_record_step_timing_round_trip(store: PostgresStore) -> None:
    """A single step timing persists and can be read back."""
    run = _make_run("run-step-1")
    await store.record_pipeline_run(run)

    timing = StepTiming(
        run_id="run-step-1",
        step_type="scan",
        step_name="ats_greenhouse",
        elapsed_ms=1234.5,
    )
    await store.record_step_timing(timing)

    # Verify via raw query
    pool = store._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM step_timings WHERE run_id = $1", "run-step-1"
        )

    assert len(rows) == 1
    assert rows[0]["step_type"] == "scan"
    assert rows[0]["step_name"] == "ats_greenhouse"
    assert rows[0]["elapsed_ms"] == pytest.approx(1234.5)
    assert rows[0]["is_error"] is False
    assert rows[0]["created_at"] is not None


async def test_record_step_timing_with_error(store: PostgresStore) -> None:
    """A step timing with is_error=True persists the flag."""
    run = _make_run("run-step-err-1")
    await store.record_pipeline_run(run)

    timing = StepTiming(
        run_id="run-step-err-1",
        step_type="evaluate",
        step_name="stage_a",
        elapsed_ms=500.0,
        is_error=True,
    )
    await store.record_step_timing(timing)

    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM step_timings WHERE run_id = $1", "run-step-err-1"
        )

    assert row is not None
    assert row["is_error"] is True


async def test_record_step_timings_batch(store: PostgresStore) -> None:
    """record_step_timings inserts multiple rows in one call."""
    run = _make_run("run-batch-1")
    await store.record_pipeline_run(run)

    timings = [
        StepTiming(
            run_id="run-batch-1",
            step_type="scan",
            step_name="ats_greenhouse",
            elapsed_ms=100.0,
        ),
        StepTiming(
            run_id="run-batch-1",
            step_type="scan",
            step_name="ats_ashby",
            elapsed_ms=200.0,
        ),
        StepTiming(
            run_id="run-batch-1",
            step_type="evaluate",
            step_name="stage_a",
            elapsed_ms=300.0,
            is_error=True,
        ),
    ]
    await store.record_step_timings(timings)

    pool = store._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM step_timings WHERE run_id = $1 ORDER BY id", "run-batch-1"
        )

    expected_count = 3
    assert len(rows) == expected_count
    assert rows[0]["step_name"] == "ats_greenhouse"
    assert rows[1]["step_name"] == "ats_ashby"
    assert rows[2]["step_name"] == "stage_a"
    assert rows[2]["is_error"] is True


async def test_record_step_timings_empty_is_noop(store: PostgresStore) -> None:
    """record_step_timings with an empty list does nothing."""
    await store.record_step_timings([])

    pool = store._get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM step_timings")

    assert count == 0
