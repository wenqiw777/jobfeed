"""SQLite batch evaluation and threshold synchronization operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import _immediate_transaction
from jobfeed.adapters.store._sqlite_values import (
    _job_from_row,
    _utc_now_text,
    _utc_text,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import JobPosting
from jobfeed.domain.scoring import MAX_STAGE_RETRIES

_CLAIM_TTL = timedelta(hours=1)


async def _get_stage_a_scores(
    lifecycle: SqliteLifecycle,
    job_ids: list[str],
) -> dict[str, int | None]:
    """Fetch scores for existing evaluation rows after strict ID parsing."""
    if not job_ids:
        return {}
    ids = [int(job_id) for job_id in job_ids]
    placeholders = ",".join("?" for _ in ids)
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            f"SELECT job_id,stage_a_score FROM evaluations "
            f"WHERE job_id IN ({placeholders})",
            ids,
        )
        rows = list(await cursor.fetchall())
        await cursor.close()
    return {str(row[0]): row[1] for row in rows}


async def _mark_stage_b_skipped_batch(
    lifecycle: SqliteLifecycle,
    job_ids: list[str],
) -> None:
    """Skip multiple evaluation rows after validating the whole ID batch."""
    if not job_ids:
        return
    ids = [int(job_id) for job_id in job_ids]
    placeholders = ",".join("?" for _ in ids)
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE evaluations SET stage_b_status='skipped_below_threshold', "
            f"updated_at=? WHERE job_id IN ({placeholders}) "
            "AND (stage_b_status IS NULL OR stage_b_status<>'completed')",
            (_utc_now_text(), *ids),
        )


async def _mark_stage_b_below_threshold(
    lifecycle: SqliteLifecycle,
    threshold: int,
    *,
    max_days: int | None,
) -> int:
    """Skip eligible Stage B rows below the active Stage A threshold."""
    now = datetime.now(UTC)
    async with lifecycle.connection() as connection:
        return await _skip_threshold_rows(
            connection,
            threshold,
            max_days=max_days,
            now=now,
        )


async def _skip_threshold_rows(
    connection: aiosqlite.Connection,
    threshold: int,
    *,
    max_days: int | None,
    now: datetime,
) -> int:
    conditions = [
        "evaluations.job_id=jobs.id",
        "evaluations.stage_a_status='completed'",
        "evaluations.stage_a_score<?",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status='error' "
        "OR (evaluations.stage_b_status='in_progress' "
        "AND evaluations.stage_b_verdict IS NULL AND evaluations.updated_at<?))",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status<>'error' "
        f"OR evaluations.stage_b_error_count<{MAX_STAGE_RETRIES})",
    ]
    params: list[object] = [threshold, _utc_text(now - _CLAIM_TTL)]
    if max_days is not None:
        conditions.append("jobs.discovered_at>=?")
        params.append(_utc_text(now - timedelta(days=max_days)))
    cursor = await connection.execute(
        "UPDATE evaluations SET stage_b_status='skipped_below_threshold', "
        "updated_at=? FROM jobs WHERE "
        + " AND ".join(conditions)
        + " RETURNING evaluations.job_id",
        (_utc_text(now), *params),
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    return len(rows)


async def _reopen_stage_b_at_or_above_threshold(
    lifecycle: SqliteLifecycle,
    threshold: int,
    *,
    max_days: int | None,
) -> int:
    """Reopen threshold-skipped rows that meet the active threshold."""
    now = datetime.now(UTC)
    async with lifecycle.connection() as connection:
        return await _reopen_threshold_rows(
            connection,
            threshold,
            max_days=max_days,
            now=now,
        )


async def _reopen_threshold_rows(
    connection: aiosqlite.Connection,
    threshold: int,
    *,
    max_days: int | None,
    now: datetime,
) -> int:
    conditions = [
        "evaluations.job_id=jobs.id",
        "evaluations.stage_a_status='completed'",
        "evaluations.stage_b_status='skipped_below_threshold'",
        "evaluations.stage_a_score>=?",
    ]
    params: list[object] = [threshold]
    if max_days is not None:
        conditions.append("jobs.discovered_at>=?")
        params.append(_utc_text(now - timedelta(days=max_days)))
    cursor = await connection.execute(
        "UPDATE evaluations SET stage_b_status=NULL, stage_b_error=NULL, "
        "updated_at=? FROM jobs WHERE "
        + " AND ".join(conditions)
        + " RETURNING evaluations.job_id",
        (_utc_text(now), *params),
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    return len(rows)


async def _sync_stage_b_threshold(
    lifecycle: SqliteLifecycle,
    threshold: int,
    *,
    max_days: int | None,
) -> tuple[int, int]:
    """Reopen and skip threshold populations in one immediate transaction."""
    now = datetime.now(UTC)
    async with (
        lifecycle.connection() as connection,
        _immediate_transaction(connection),
    ):
        reopened = await _reopen_threshold_rows(
            connection,
            threshold,
            max_days=max_days,
            now=now,
        )
        await _after_threshold_reopen(connection)
        skipped = await _skip_threshold_rows(
            connection,
            threshold,
            max_days=max_days,
            now=now,
        )
    return reopened, skipped


async def _after_threshold_reopen(connection: aiosqlite.Connection) -> None:
    """Provide a deterministic rollback injection boundary for contract tests."""
    del connection


async def _preview_pending_stage_b_after_threshold_sync(
    lifecycle: SqliteLifecycle,
    *,
    limit: int,
    max_days: int | None,
    stage_a_threshold: int,
) -> list[JobPosting]:
    """Preview post-sync Stage B eligibility without changing stored rows."""
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    now = datetime.now(UTC)
    conditions = [
        "evaluations.stage_a_status='completed'",
        "evaluations.stage_a_score>=?",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status<>'error' "
        f"OR evaluations.stage_b_error_count<{MAX_STAGE_RETRIES})",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status='error' "
        "OR evaluations.stage_b_status='skipped_below_threshold' "
        "OR (evaluations.stage_b_status='in_progress' "
        "AND evaluations.updated_at<? AND evaluations.stage_b_verdict IS NULL))",
    ]
    params: list[object] = [stage_a_threshold, _utc_text(now - _CLAIM_TTL)]
    if max_days is not None:
        conditions.append("jobs.discovered_at>=?")
        params.append(_utc_text(now - timedelta(days=max_days)))
    params.append(limit)
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            "SELECT jobs.* FROM jobs JOIN evaluations ON evaluations.job_id=jobs.id "
            "WHERE "
            + " AND ".join(conditions)
            + " ORDER BY jobs.discovered_at DESC, jobs.id DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [_job_from_row(row) for row in rows]
