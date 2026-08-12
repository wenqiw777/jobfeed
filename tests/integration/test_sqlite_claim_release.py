"""Integration contracts for restoring and refreshing SQLite evaluation claims."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.support.sqlite_claims_fixtures import (
    _seed_evaluation as seed_evaluation,
)
from tests.support.sqlite_claims_fixtures import (
    _seed_job as seed_job,
)
from tests.support.sqlite_claims_fixtures import (
    _sqlite_timestamp as sqlite_timestamp,
)

_NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


async def test_release_restores_every_stage_a_and_stage_b_prior_state(
    tmp_path: Path,
) -> None:
    """Error evidence wins, then completed evidence, then the NULL state."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    claims = SqliteClaimsRuns(lifecycle)
    async with lifecycle.connection() as connection:
        a_error = await _seed_claim(connection, "a-error", stage="a", evidence="error")
        a_done = await _seed_claim(connection, "a-done", stage="a", evidence="done")
        a_null = await _seed_claim(connection, "a-null", stage="a", evidence="null")
        b_error = await _seed_claim(connection, "b-error", stage="b", evidence="error")
        b_done = await _seed_claim(connection, "b-done", stage="b", evidence="done")
        b_null = await _seed_claim(connection, "b-null", stage="b", evidence="null")

    release_at = _NOW + timedelta(minutes=1)
    for job_id in (a_error, a_done, a_null):
        await claims.release_stage_a_claim(job_id, now=release_at)
    for job_id in (b_error, b_done, b_null):
        await claims.release_stage_b_claim(job_id, now=release_at)

    stage_a_states = await _states(lifecycle, "stage_a_status")
    assert {job_id: stage_a_states[job_id] for job_id in (a_error, a_done, a_null)} == {
        a_error: "error",
        a_done: "completed",
        a_null: None,
    }
    stage_b_states = await _states(lifecycle, "stage_b_status")
    assert {job_id: stage_b_states[job_id] for job_id in (b_error, b_done, b_null)} == {
        b_error: "error",
        b_done: "completed",
        b_null: None,
    }
    assert await _updated(lifecycle, a_error) == sqlite_timestamp(release_at)

    await claims.release_stage_a_claim(a_error, now=release_at + timedelta(minutes=1))
    assert await _updated(lifecycle, a_error) == sqlite_timestamp(release_at)
    await lifecycle.close()


async def test_release_and_refresh_missing_or_inactive_rows_are_no_ops(
    tmp_path: Path,
) -> None:
    """Missing and non-in-progress rows do not receive claim timestamps."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    claims = SqliteClaimsRuns(lifecycle)
    async with lifecycle.connection() as connection:
        job_id = await seed_job(
            connection,
            canonical_id="inactive",
            discovered_at=_NOW,
        )
        await seed_evaluation(
            connection,
            job_id=job_id,
            updated_at=_NOW,
            stage_a_status="completed",
            stage_a_score=90,
            stage_b_status="completed",
            stage_b_verdict="apply",
        )

    later = _NOW + timedelta(hours=1)
    await claims.release_stage_a_claim(job_id, now=later)
    await claims.release_stage_b_claim(job_id, now=later)
    await claims.refresh_stage_b_claim(job_id, now=later)
    await claims.release_stage_a_claim("999999", now=later)
    assert await _updated(lifecycle, job_id) == sqlite_timestamp(_NOW)
    with pytest.raises(ValueError):
        await claims.release_stage_a_claim("bad", now=later)
    await lifecycle.close()


async def _seed_claim(
    connection: aiosqlite.Connection,
    canonical_id: str,
    *,
    stage: str,
    evidence: str,
) -> str:
    job_id = await seed_job(
        connection,
        canonical_id=canonical_id,
        discovered_at=_NOW,
    )
    values: dict[str, object] = {
        "job_id": job_id,
        "updated_at": _NOW,
        f"stage_{stage}_status": "in_progress",
    }
    if evidence == "error":
        values[f"stage_{stage}_error"] = "retry"
    elif evidence == "done":
        values["stage_a_score" if stage == "a" else "stage_b_verdict"] = (
            80 if stage == "a" else "apply"
        )
    await seed_evaluation(connection, **values)
    return job_id


async def _states(lifecycle: SqliteLifecycle, column: str) -> dict[str, object]:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            f"SELECT job_id, {column} FROM evaluations "
            f"WHERE {column} IS NOT 'in_progress' ORDER BY job_id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return {str(job_id): status for job_id, status in rows if status is not None} | {
        str(job_id): None for job_id, status in rows if status is None
    }


async def _updated(lifecycle: SqliteLifecycle, job_id: str) -> object:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT updated_at FROM evaluations WHERE job_id=?",
            (int(job_id),),
        )
        row = await cursor.fetchone()
        await cursor.close()
    assert row is not None
    return row[0]
