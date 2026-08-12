"""SQLite interview-round commands with serialized index assignment."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _immediate_transaction,
    _parse_utc_timestamp,
    _require_utc_timestamp,
)
from jobfeed.domain.interview import InterviewRound

if TYPE_CHECKING:
    from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class _SqliteInterviews:
    """Implement retained interview commands over an injected lifecycle."""

    _lifecycle: SqliteLifecycle

    def _application_time(self, value: datetime | None = None) -> datetime:
        raise NotImplementedError

    async def add_interview_round(
        self,
        *,
        job_id: str,
        label: str,
        scheduled_at: datetime | None = None,
    ) -> InterviewRound:
        """Append a uniquely indexed round and bump activity in one transaction."""
        numeric_id = int(job_id)
        now = self._application_time()
        schedule = (
            _require_utc_timestamp(self._application_time(scheduled_at), "scheduled_at")
            if scheduled_at is not None
            else None
        )
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """INSERT INTO interview_rounds
                   (job_id,round_index,label,scheduled_at,created_at)
                   SELECT ?,COALESCE(MAX(round_index),0)+1,?,?,?
                   FROM interview_rounds WHERE job_id=? RETURNING *""",
                (
                    numeric_id,
                    label,
                    schedule,
                    _require_utc_timestamp(now),
                    numeric_id,
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise RuntimeError("SQLite interview insert returned no row")
            await self._after_round_mutation("add", connection)
            await _bump_status_clock(connection, numeric_id, now)
        return _interview_round(row)

    async def list_interview_rounds(self, job_id: str) -> list[InterviewRound]:
        """List a job's rounds in strict ascending round-index order."""
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                "SELECT * FROM interview_rounds WHERE job_id=? "
                "ORDER BY round_index ASC",
                (int(job_id),),
            )
        return [_interview_round(row) for row in rows]

    async def complete_interview_round(
        self,
        *,
        job_id: str,
        round_index: int | None = None,
        notes: str | None = None,
    ) -> InterviewRound:
        """Complete one still-open round and bump activity atomically."""
        numeric_id = int(job_id)
        now = self._application_time()
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            connection.row_factory = aiosqlite.Row
            target = await _open_round(connection, numeric_id, round_index)
            if target is None:
                raise ValueError(f"no open interview round for job_id={job_id}")
            cursor = await connection.execute(
                """UPDATE interview_rounds SET completed_at=?,
                   notes=CASE WHEN ? IS NULL THEN notes ELSE ? END
                   WHERE id=? AND completed_at IS NULL RETURNING *""",
                (_require_utc_timestamp(now), notes, notes, target["id"]),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise ValueError(f"no open interview round for job_id={job_id}")
            await self._after_round_mutation("complete", connection)
            await _bump_status_clock(connection, numeric_id, now)
        return _interview_round(row)

    async def _after_round_mutation(
        self,
        operation: str,
        connection: aiosqlite.Connection,
    ) -> None:
        del operation, connection


async def _open_round(
    connection: aiosqlite.Connection,
    job_id: int,
    round_index: int | None,
) -> aiosqlite.Row | None:
    if round_index is None:
        return await _fetch_row(
            connection,
            "SELECT * FROM interview_rounds WHERE job_id=? "
            "AND completed_at IS NULL ORDER BY round_index DESC LIMIT 1",
            (job_id,),
        )
    return await _fetch_row(
        connection,
        "SELECT * FROM interview_rounds WHERE job_id=? AND round_index=? "
        "AND completed_at IS NULL",
        (job_id, round_index),
    )


async def _bump_status_clock(
    connection: aiosqlite.Connection,
    job_id: int,
    now: datetime,
) -> None:
    await connection.execute(
        "UPDATE job_status SET last_status_change_at=? WHERE job_id=?",
        (_require_utc_timestamp(now), job_id),
    )


def _interview_round(row: aiosqlite.Row) -> InterviewRound:
    return InterviewRound(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        round_index=int(row["round_index"]),
        label=str(row["label"]),
        scheduled_at=(
            _parse_utc_timestamp(row["scheduled_at"])
            if row["scheduled_at"] is not None
            else None
        ),
        completed_at=(
            _parse_utc_timestamp(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
        notes=row["notes"],
        created_at=_parse_utc_timestamp(row["created_at"]),
    )
