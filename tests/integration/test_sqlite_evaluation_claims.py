"""Integration contracts for SQLite evaluation claim capabilities."""

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


async def _open_capability(tmp_path: Path) -> tuple[SqliteLifecycle, SqliteClaimsRuns]:
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    return lifecycle, SqliteClaimsRuns(lifecycle)


async def test_stage_a_preview_and_claim_share_strict_eligibility(
    tmp_path: Path,
) -> None:
    """Preview is non-mutating and claim obeys stale, retry, closed, and order."""
    lifecycle, claims = await _open_capability(tmp_path)
    async with lifecycle.connection() as connection:
        newest = await seed_job(connection, canonical_id="newest", discovered_at=_NOW)
        stale = await seed_job(
            connection, canonical_id="stale", discovered_at=_NOW - timedelta(minutes=1)
        )
        exact = await seed_job(
            connection, canonical_id="exact", discovered_at=_NOW - timedelta(minutes=2)
        )
        closed = await seed_job(
            connection,
            canonical_id="closed",
            discovered_at=_NOW + timedelta(minutes=1),
            closed_at=_NOW,
        )
        capped = await seed_job(
            connection, canonical_id="capped", discovered_at=_NOW + timedelta(minutes=2)
        )
        await seed_evaluation(
            connection,
            job_id=stale,
            updated_at=_NOW - timedelta(hours=1, microseconds=1),
            stage_a_status="in_progress",
        )
        await seed_evaluation(
            connection,
            job_id=exact,
            updated_at=_NOW - timedelta(hours=1),
            stage_a_status="in_progress",
        )
        await seed_evaluation(
            connection,
            job_id=capped,
            updated_at=_NOW,
            stage_a_status="error",
            stage_a_error="failed",
            stage_a_error_count=3,
        )

    preview = await claims.preview_claimable_stage_a(now=_NOW, corpus="unrated")
    assert [job.id for job in preview] == [newest, stale]
    assert await _stage_a_status(lifecycle, newest) is None

    claimed = await claims.claim_pending_stage_a(now=_NOW, corpus="unrated")
    assert [job.id for job in claimed] == [newest, stale]
    assert await claims.claim_pending_stage_a(now=_NOW, corpus="unrated") == []
    assert await _stage_a_status(lifecycle, exact) == "in_progress"
    assert await _stage_a_status(lifecycle, closed) is None
    await lifecycle.close()


async def test_gate_candidates_keyset_twin_and_gate_filters(tmp_path: Path) -> None:
    """Gate loading is read-only, keyset-stable, and suppresses scored twins."""
    lifecycle, claims = await _open_capability(tmp_path)
    async with lifecycle.connection() as connection:
        completed = await seed_job(
            connection,
            canonical_id="completed",
            discovered_at=_NOW + timedelta(minutes=2),
            company_norm="example",
            title_norm="engineer",
        )
        twin = await seed_job(
            connection,
            canonical_id="twin",
            discovered_at=_NOW + timedelta(minutes=1),
            company_norm="example",
            title_norm="engineer",
        )
        failed_gate = await seed_job(
            connection,
            canonical_id="gate-fail",
            discovered_at=_NOW,
            gate_result="fail",
        )
        first = await seed_job(
            connection,
            canonical_id="first",
            discovered_at=_NOW - timedelta(minutes=1),
        )
        second = await seed_job(
            connection,
            canonical_id="second",
            discovered_at=_NOW - timedelta(minutes=1),
        )
        await seed_evaluation(
            connection,
            job_id=completed,
            updated_at=_NOW,
            stage_a_status="completed",
            stage_a_score=90,
        )

    page = await claims.load_gate_candidates(now=_NOW, limit=1)
    assert [candidate.job.id for candidate in page] == [second]
    cursor = (page[0].job.discovered_at, int(page[0].job.id or "0"))
    next_page = await claims.load_gate_candidates(now=_NOW, limit=5, after=cursor)
    assert [candidate.job.id for candidate in next_page] == [first]
    assert twin not in [candidate.job.id for candidate in page + next_page]
    assert failed_gate not in [candidate.job.id for candidate in page + next_page]

    all_rows = await claims.load_gate_candidates(
        now=_NOW,
        corpus="all",
        exclude_gate_failed=False,
    )
    assert completed in [candidate.job.id for candidate in all_rows]
    assert failed_gate in [candidate.job.id for candidate in all_rows]
    await lifecycle.close()


async def test_claim_by_ids_drops_malformed_duplicates_and_preserves_db_order(
    tmp_path: Path,
) -> None:
    """ID-restricted claiming ignores malformed IDs and claims each row once."""
    lifecycle, claims = await _open_capability(tmp_path)
    async with lifecycle.connection() as connection:
        older = await seed_job(connection, canonical_id="older", discovered_at=_NOW)
        newer = await seed_job(
            connection, canonical_id="newer", discovered_at=_NOW + timedelta(seconds=1)
        )

    claimed = await claims.claim_stage_a_by_ids(
        [older, "bad-id", newer, older],
        now=_NOW,
    )
    assert [job.id for job in claimed] == [newer, older]
    assert await claims.claim_stage_a_by_ids([], now=_NOW) == []
    assert await claims.claim_stage_a_by_ids(["bad"], now=_NOW) == []
    await lifecycle.close()


async def test_stage_b_claim_release_and_refresh_strict_boundaries(
    tmp_path: Path,
) -> None:
    """Stage B claiming, release restoration, and heartbeat use caller time."""
    lifecycle, claims = await _open_capability(tmp_path)
    async with lifecycle.connection() as connection:
        pending = await seed_job(connection, canonical_id="pending", discovered_at=_NOW)
        stale = await seed_job(
            connection, canonical_id="stale", discovered_at=_NOW - timedelta(minutes=1)
        )
        exact = await seed_job(
            connection, canonical_id="exact", discovered_at=_NOW - timedelta(minutes=2)
        )
        await seed_evaluation(
            connection,
            job_id=pending,
            updated_at=_NOW,
            stage_a_status="completed",
            stage_a_score=80,
        )
        await seed_evaluation(
            connection,
            job_id=stale,
            updated_at=_NOW - timedelta(hours=1, microseconds=1),
            stage_a_status="completed",
            stage_a_score=80,
            stage_b_status="in_progress",
        )
        await seed_evaluation(
            connection,
            job_id=exact,
            updated_at=_NOW - timedelta(hours=1),
            stage_a_status="completed",
            stage_a_score=80,
            stage_b_status="in_progress",
        )

    claimed = await claims.claim_pending_stage_b(now=_NOW, stage_a_threshold=75)
    assert [job.id for job in claimed] == [pending, stale]
    await claims.refresh_stage_b_claim(pending, now=_NOW + timedelta(minutes=30))
    assert await _updated_at(lifecycle, pending) == sqlite_timestamp(
        _NOW + timedelta(minutes=30)
    )
    await claims.release_stage_b_claim(pending, now=_NOW + timedelta(minutes=31))
    assert await _stage_b_status(lifecycle, pending) is None
    await claims.release_stage_b_claim(pending, now=_NOW + timedelta(minutes=32))
    assert await _stage_b_status(lifecycle, exact) == "in_progress"
    await lifecycle.close()


async def test_claim_failure_rolls_back_every_selected_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An injected failure after selection leaves all evaluation rows untouched."""
    lifecycle, claims = await _open_capability(tmp_path)
    async with lifecycle.connection() as connection:
        job_id = await seed_job(connection, canonical_id="rollback", discovered_at=_NOW)

    async def fail(_stage: str, _connection: aiosqlite.Connection) -> None:
        raise RuntimeError("injected claim failure")

    monkeypatch.setattr(claims, "_after_claim_selection", fail)
    with pytest.raises(RuntimeError, match="injected claim failure"):
        await claims.claim_pending_stage_a(now=_NOW)
    assert await _stage_a_status(lifecycle, job_id) is None
    await lifecycle.close()


async def test_claim_inputs_fail_before_mutation(tmp_path: Path) -> None:
    """Naive time, unknown corpus, and negative limit fail closed."""
    lifecycle, claims = await _open_capability(tmp_path)
    with pytest.raises(ValueError, match="aware"):
        await claims.claim_pending_stage_a(now=datetime(2026, 8, 12))
    with pytest.raises(ValueError, match="corpus"):
        await claims.claim_pending_stage_a(now=_NOW, corpus="unknown")
    with pytest.raises(ValueError, match="limit"):
        await claims.claim_pending_stage_a(now=_NOW, limit=-1)
    await lifecycle.close()


async def _value(lifecycle: SqliteLifecycle, sql: str, job_id: str) -> object:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(sql, (int(job_id),))
        row = await cursor.fetchone()
        await cursor.close()
    return None if row is None else row[0]


async def _stage_a_status(lifecycle: SqliteLifecycle, job_id: str) -> object:
    return await _value(
        lifecycle, "SELECT stage_a_status FROM evaluations WHERE job_id=?", job_id
    )


async def _stage_b_status(lifecycle: SqliteLifecycle, job_id: str) -> object:
    return await _value(
        lifecycle, "SELECT stage_b_status FROM evaluations WHERE job_id=?", job_id
    )


async def _updated_at(lifecycle: SqliteLifecycle, job_id: str) -> object:
    return await _value(
        lifecycle, "SELECT updated_at FROM evaluations WHERE job_id=?", job_id
    )
