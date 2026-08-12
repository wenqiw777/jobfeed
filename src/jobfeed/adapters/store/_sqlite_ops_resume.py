"""SQLite retained resume snapshot reads and variant registration."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import _fetch_row, _fetch_rows
from jobfeed.adapters.store._sqlite_values import _datetime_from_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import ResumeSnapshot, ResumeSnapshotSummary


async def _get_resume_snapshot(
    lifecycle: SqliteLifecycle,
    resume_hash: str,
) -> ResumeSnapshot | None:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection,
            "SELECT resume_hash,captured_at,source,content,notes "
            "FROM resume_snapshots WHERE resume_hash=?",
            (resume_hash,),
        )
    return _snapshot_from_row(row) if row is not None else None


async def _get_resume_snapshot_by_prefix(
    lifecycle: SqliteLifecycle,
    prefix: str,
) -> ResumeSnapshot:
    async with lifecycle.connection() as connection:
        rows = await _fetch_rows(
            connection,
            "SELECT resume_hash,captured_at,source,content,notes "
            "FROM resume_snapshots WHERE substr(resume_hash,1,length(?))=? LIMIT 2",
            (prefix, prefix),
        )
    if not rows:
        raise SnapshotNotFoundError(f"no resume snapshot matches prefix {prefix!r}")
    if len(rows) > 1:
        raise SnapshotAmbiguousError(
            f"resume hash prefix {prefix!r} matches multiple snapshots"
        )
    return _snapshot_from_row(rows[0])


async def _list_resume_snapshots(
    lifecycle: SqliteLifecycle,
    source: str | None,
) -> list[ResumeSnapshotSummary]:
    condition = "TRUE" if source is None else "s.source=?"
    params: tuple[object, ...] = () if source is None else (source,)
    async with lifecycle.connection() as connection:
        rows = await _fetch_rows(
            connection,
            "SELECT s.resume_hash,s.captured_at,s.source,COUNT(a.job_id) usage_count "
            "FROM resume_snapshots s LEFT JOIN applied a ON "
            "a.master_resume_hash=s.resume_hash OR "
            "a.tailored_resume_hash=s.resume_hash WHERE "
            + condition
            + " GROUP BY s.resume_hash,s.captured_at,s.source "
            "ORDER BY s.captured_at DESC,s.resume_hash ASC",
            params,
        )
    return [
        ResumeSnapshotSummary(
            resume_hash=str(row["resume_hash"]),
            captured_at=_required_time(row["captured_at"]),
            source=str(row["source"]),
            usage_count=int(row["usage_count"]),
        )
        for row in rows
    ]


async def _register_resume_variant(
    lifecycle: SqliteLifecycle,
    *,
    name: str,
    description: str | None,
) -> bool:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "INSERT INTO resume_variants(name,description) VALUES (?,?) "
            "ON CONFLICT(name) DO NOTHING RETURNING name",
            (name, description),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return row is not None


def _snapshot_from_row(row: aiosqlite.Row) -> ResumeSnapshot:
    return ResumeSnapshot(
        resume_hash=str(row["resume_hash"]),
        captured_at=_required_time(row["captured_at"]),
        source=str(row["source"]),
        content=str(row["content"]),
        notes=row["notes"],
    )


def _required_time(value: object) -> datetime:
    parsed = _datetime_from_text(value)
    if parsed is None:
        raise ValueError("resume snapshot captured_at is NULL")
    return parsed
