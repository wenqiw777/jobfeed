"""Reusable run fixtures and SQLite assertions for run-lease tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models_run import PipelineRun

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)
OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"


def _run_fixture(number: int, *, status: str = "running") -> PipelineRun:
    """Build a complete deterministic run snapshot."""
    return PipelineRun(
        run_id=str(UUID(int=number, version=4)),
        started_at=NOW,
        source="test",
        status=status,
        jobs_discovered=number,
        jobs_inserted=number + 1,
        jobs_updated=number + 2,
        jobs_filtered=number + 3,
        jobs_ml_gated=number + 4,
        jobs_gate_passed=number + 5,
        stage_a_scored=number + 6,
        stage_b_scored=number + 7,
        jobs_scored=number + 8,
        total_llm_cost_usd=float(number) / 100,
        errors=number + 9,
        finished_at=NOW if status != "running" else None,
    )


def _terminal_run(run: PipelineRun, finished_at: datetime) -> PipelineRun:
    """Copy a running fixture into a succeeded terminal snapshot."""
    values = vars(run) | {"status": "succeeded", "finished_at": finished_at}
    return PipelineRun(**values)


async def _query_one(
    lifecycle: SqliteLifecycle,
    sql: str,
    params: tuple[object, ...],
) -> tuple[object, ...] | None:
    """Return one SQLite row as a plain tuple."""
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
    return tuple(row) if row is not None else None


async def _run_state(
    lifecycle: SqliteLifecycle, run_id: str
) -> tuple[object, ...] | None:
    """Return persisted run status and finish time."""
    return await _query_one(
        lifecycle,
        "SELECT status, finished_at FROM pipeline_runs WHERE run_id=?",
        (run_id,),
    )


async def _run_counters(
    lifecycle: SqliteLifecycle, run_id: str
) -> tuple[object, ...] | None:
    """Return the representative terminal run counters."""
    return await _query_one(
        lifecycle,
        "SELECT status, jobs_discovered, jobs_gate_passed, errors "
        "FROM pipeline_runs WHERE run_id=?",
        (run_id,),
    )


async def _lease_owner(
    lifecycle: SqliteLifecycle, kind: str
) -> tuple[object, ...] | None:
    """Return generation and current fencing identities for one kind."""
    return await _query_one(
        lifecycle,
        "SELECT generation, owner_id, run_id FROM run_leases WHERE kind=?",
        (kind,),
    )


async def _lease_expiry(lifecycle: SqliteLifecycle, kind: str) -> object:
    """Return the stored expiry for one permanent lease row."""
    row = await _query_one(
        lifecycle,
        "SELECT expires_at FROM run_leases WHERE kind=?",
        (kind,),
    )
    assert row is not None
    return row[0]


async def _pipeline_count(lifecycle: SqliteLifecycle) -> int:
    """Count persisted pipeline runs."""
    row = await _query_one(lifecycle, "SELECT COUNT(*) FROM pipeline_runs", ())
    assert row is not None
    return int(row[0])
