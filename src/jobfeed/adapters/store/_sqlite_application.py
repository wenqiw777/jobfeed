"""SQLite atomic application audit and resume snapshot projections."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _immediate_transaction,
    _parse_utc_timestamp,
    _placeholders,
    _require_utc_timestamp,
)
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    ResumeSnapshot,
    ResumeSnapshotSummary,
    ResumeVariantStats,
)
from jobfeed.domain.status import (
    ACTIVE_APPLICATION_STATUSES,
    DEFAULT_FOLLOWUP_GRACE_DAYS,
    RESPONSE_STATUSES,
    is_terminal,
)

if TYPE_CHECKING:
    from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle

_APPLICATION_COLUMNS = """job_id,applied_at,master_resume_hash,
    tailored_resume_hash,cover_letter,application_method,verdict_snapshot,
    fit_snapshot,hooks_snapshot,notes"""
_INTERVIEW_STATUSES = frozenset({"interviewing", "offer"})


class _SqliteApplication:
    """Implement the authoritative apply aggregate and retained projections."""

    _lifecycle: SqliteLifecycle

    def _application_time(self, value: datetime | None = None) -> datetime:
        raise NotImplementedError

    async def record_application_with_snapshots(
        self,
        record: ApplicationRecord,
        *,
        snapshots: list[ResumeSnapshot] | None = None,
        resume_variant: str | None = None,
    ) -> bool:
        """Atomically persist variant, snapshots, audit, status, and history."""
        numeric_id = int(record.job_id)
        now = self._application_time()
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            if resume_variant is not None:
                await _register_variant(connection, resume_variant, None, now)
            for snapshot in snapshots or []:
                await _save_snapshot(connection, snapshot)
            inserted = await _insert_application(connection, record)
            if not inserted:
                return False
            await self._before_application_guard(connection)
            status_row = await _fetch_row(
                connection,
                "SELECT status FROM job_status WHERE job_id=?",
                (numeric_id,),
            )
            if status_row is None:
                raise KeyError(f"no status row for job_id={record.job_id}")
            old_status = str(status_row["status"])
            if is_terminal(old_status):
                raise ValueError(f"cannot apply from terminal status '{old_status}'")
            if old_status in ACTIVE_APPLICATION_STATUSES:
                await _record_active_application(
                    connection,
                    numeric_id,
                    old_status,
                    resume_variant,
                    now,
                )
            else:
                await _record_new_application_status(
                    connection,
                    numeric_id,
                    old_status,
                    resume_variant,
                    now,
                )
        return True

    async def get_application(self, job_id: str) -> ApplicationRecord | None:
        """Load one application audit row by decimal job identity."""
        async with self._lifecycle.connection() as connection:
            row = await _fetch_row(
                connection,
                f"SELECT {_APPLICATION_COLUMNS} FROM applied WHERE job_id=?",
                (int(job_id),),
            )
        return _application_record(row) if row is not None else None

    async def list_applications(
        self,
        *,
        limit: int = 100,
        resume_hash_prefix: str | None = None,
    ) -> list[ApplicationRecord]:
        """List recent audits, optionally matching a literal hash prefix."""
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        params: list[object] = []
        where = ""
        if resume_hash_prefix is not None:
            where = (
                "WHERE substr(master_resume_hash,1,length(?))=? "
                "OR substr(tailored_resume_hash,1,length(?))=?"
            )
            params.extend([resume_hash_prefix] * 4)
        params.append(limit)
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                f"SELECT {_APPLICATION_COLUMNS} FROM applied {where} "
                "ORDER BY applied_at DESC LIMIT ?",
                params,
            )
        return [_application_record(row) for row in rows]

    async def get_resume_snapshot(
        self,
        resume_hash: str,
    ) -> ResumeSnapshot | None:
        """Load one content-addressed resume snapshot by exact hash."""
        async with self._lifecycle.connection() as connection:
            row = await _fetch_row(
                connection,
                "SELECT * FROM resume_snapshots WHERE resume_hash=?",
                (resume_hash,),
            )
        return _resume_snapshot(row) if row is not None else None

    async def get_resume_snapshot_by_prefix(self, prefix: str) -> ResumeSnapshot:
        """Resolve one literal case-sensitive hash prefix or raise typed errors."""
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                "SELECT * FROM resume_snapshots "
                "WHERE substr(resume_hash,1,length(?))=? LIMIT 2",
                (prefix, prefix),
            )
        if not rows:
            raise SnapshotNotFoundError(f"no resume snapshot matches prefix {prefix!r}")
        if len(rows) > 1:
            raise SnapshotAmbiguousError(
                f"resume hash prefix {prefix!r} matches multiple snapshots"
            )
        return _resume_snapshot(rows[0])

    async def list_resume_snapshots(
        self,
        source: str | None = None,
    ) -> list[ResumeSnapshotSummary]:
        """List snapshot provenance and distinct applied-row usage counts."""
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                """SELECT s.resume_hash,s.captured_at,s.source,
                          COUNT(a.job_id) AS usage_count
                   FROM resume_snapshots s LEFT JOIN applied a
                     ON a.master_resume_hash=s.resume_hash
                     OR a.tailored_resume_hash=s.resume_hash
                   WHERE ? IS NULL OR s.source=?
                   GROUP BY s.resume_hash,s.captured_at,s.source
                   ORDER BY s.captured_at DESC,s.resume_hash ASC""",
                (source, source),
            )
        return [
            ResumeSnapshotSummary(
                resume_hash=str(row["resume_hash"]),
                captured_at=_parse_utc_timestamp(row["captured_at"]),
                source=str(row["source"]),
                usage_count=int(row["usage_count"]),
            )
            for row in rows
        ]

    async def register_resume_variant(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> bool:
        """Register a first-write-wins resume variant."""
        async with self._lifecycle.connection() as connection:
            return await _register_variant(
                connection,
                name,
                description,
                self._application_time(),
            )

    async def application_stats(
        self,
        *,
        since_days_ago: int | None = 30,
        by_resume: bool = False,
    ) -> ApplicationStats:
        """Aggregate causal append-order outcomes for the application cohort."""
        cutoff = (
            None
            if since_days_ago is None
            else _require_utc_timestamp(
                self._application_time() - timedelta(days=since_days_ago)
            )
        )
        async with self._lifecycle.connection() as connection:
            applied_rows = await _fetch_rows(
                connection,
                """SELECT id,job_id,changed_at,resume_variant_at_change
                   FROM job_status_history WHERE to_status='applied'
                     AND (? IS NULL OR changed_at>=?) ORDER BY id ASC""",
                (cutoff, cutoff),
            )
            first_applied = _first_apply_by_job(applied_rows)
            if not first_applied:
                return _empty_stats()
            job_ids = sorted(first_applied)
            histories = await _fetch_rows(
                connection,
                f"""SELECT id,job_id,to_status,changed_at
                    FROM job_status_history WHERE job_id IN
                    ({_placeholders(job_ids)}) ORDER BY id ASC""",
                job_ids,
            )
        outcomes, deltas = _causal_outcomes(first_applied, histories)
        response_count, interview_count, offer_count, rejection_count = _count_outcomes(
            outcomes
        )
        resume_stats = _by_resume_stats(first_applied, outcomes) if by_resume else None
        return ApplicationStats(
            applied_count=len(first_applied),
            response_count=response_count,
            interview_count=interview_count,
            offer_count=offer_count,
            rejection_count=rejection_count,
            median_days_to_response=_median(deltas),
            by_resume=resume_stats,
        )

    async def _before_application_guard(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        del connection


async def _save_snapshot(
    connection: aiosqlite.Connection,
    snapshot: ResumeSnapshot,
) -> None:
    await connection.execute(
        """INSERT INTO resume_snapshots
           (resume_hash,captured_at,source,content,notes) VALUES (?,?,?,?,?)
           ON CONFLICT(resume_hash) DO NOTHING""",
        (
            snapshot.resume_hash,
            _require_utc_timestamp(snapshot.captured_at, "snapshot.captured_at"),
            snapshot.source,
            snapshot.content,
            snapshot.notes,
        ),
    )


async def _register_variant(
    connection: aiosqlite.Connection,
    name: str,
    description: str | None,
    now: datetime,
) -> bool:
    cursor = await connection.execute(
        """INSERT INTO resume_variants(name,description,created_at)
           VALUES (?,?,?) ON CONFLICT(name) DO NOTHING""",
        (name, description, _require_utc_timestamp(now)),
    )
    changed = cursor.rowcount
    await cursor.close()
    return changed == 1


async def _insert_application(
    connection: aiosqlite.Connection,
    record: ApplicationRecord,
) -> bool:
    cursor = await connection.execute(
        """INSERT INTO applied
           (job_id,applied_at,notes,master_resume_hash,tailored_resume_hash,
            cover_letter,application_method,verdict_snapshot,fit_snapshot,
            hooks_snapshot) VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(job_id) DO NOTHING""",
        (
            int(record.job_id),
            _require_utc_timestamp(record.applied_at, "record.applied_at"),
            record.notes,
            record.master_resume_hash,
            record.tailored_resume_hash,
            record.cover_letter,
            record.application_method,
            record.verdict_snapshot,
            record.fit_snapshot,
            record.hooks_snapshot,
        ),
    )
    changed = cursor.rowcount
    await cursor.close()
    return changed == 1


async def _record_active_application(
    connection: aiosqlite.Connection,
    job_id: int,
    status: str,
    variant: str | None,
    now: datetime,
) -> None:
    await connection.execute(
        """INSERT INTO job_status_history
           (job_id,from_status,to_status,changed_at,reason,resume_variant_at_change)
           VALUES (?,?,?,?,?,?)""",
        (
            job_id,
            status,
            status,
            _require_utc_timestamp(now),
            "record_application_noop",
            variant,
        ),
    )
    if variant:
        await connection.execute(
            "UPDATE job_status SET resume_variant=? WHERE job_id=?",
            (variant, job_id),
        )


async def _record_new_application_status(
    connection: aiosqlite.Connection,
    job_id: int,
    old_status: str,
    variant: str | None,
    now: datetime,
) -> None:
    timestamp = _require_utc_timestamp(now)
    followup = _require_utc_timestamp(now + timedelta(days=DEFAULT_FOLLOWUP_GRACE_DAYS))
    await connection.execute(
        """UPDATE job_status SET status='applied',last_status_change_at=?,
           next_followup_at=?,resume_variant=COALESCE(?,resume_variant)
           WHERE job_id=?""",
        (timestamp, followup, variant, job_id),
    )
    await connection.execute(
        """INSERT INTO job_status_history
           (job_id,from_status,to_status,changed_at,reason,resume_variant_at_change)
           VALUES (?,?,'applied',?,'record_application',?)""",
        (job_id, old_status, timestamp, variant),
    )


def _application_record(row: aiosqlite.Row) -> ApplicationRecord:
    return ApplicationRecord(
        job_id=str(row["job_id"]),
        applied_at=_parse_utc_timestamp(row["applied_at"]),
        master_resume_hash=row["master_resume_hash"],
        tailored_resume_hash=row["tailored_resume_hash"],
        cover_letter=row["cover_letter"],
        application_method=row["application_method"],
        verdict_snapshot=row["verdict_snapshot"],
        fit_snapshot=row["fit_snapshot"],
        hooks_snapshot=row["hooks_snapshot"],
        notes=row["notes"],
    )


def _resume_snapshot(row: aiosqlite.Row) -> ResumeSnapshot:
    return ResumeSnapshot(
        resume_hash=str(row["resume_hash"]),
        captured_at=_parse_utc_timestamp(row["captured_at"]),
        source=str(row["source"]),
        content=str(row["content"]),
        notes=row["notes"],
    )


def _first_apply_by_job(rows: list[aiosqlite.Row]) -> dict[int, aiosqlite.Row]:
    first: dict[int, aiosqlite.Row] = {}
    for row in rows:
        first.setdefault(int(row["job_id"]), row)
    return first


def _causal_outcomes(
    first_applied: dict[int, aiosqlite.Row],
    histories: list[aiosqlite.Row],
) -> tuple[dict[int, set[str]], list[int]]:
    outcomes: dict[int, set[str]] = {}
    first_response_at: dict[int, datetime] = {}
    for row in histories:
        job_id = int(row["job_id"])
        applied = first_applied[job_id]
        status = str(row["to_status"])
        if int(row["id"]) <= int(applied["id"]) or status not in RESPONSE_STATUSES:
            continue
        outcomes.setdefault(job_id, set()).add(status)
        first_response_at.setdefault(job_id, _parse_utc_timestamp(row["changed_at"]))
    deltas = [
        max(
            0,
            (
                response_at - _parse_utc_timestamp(first_applied[job_id]["changed_at"])
            ).days,
        )
        for job_id, response_at in first_response_at.items()
    ]
    return outcomes, deltas


def _count_outcomes(outcomes: dict[int, set[str]]) -> tuple[int, int, int, int]:
    return (
        len(outcomes),
        sum(bool(statuses & _INTERVIEW_STATUSES) for statuses in outcomes.values()),
        sum("offer" in statuses for statuses in outcomes.values()),
        sum("rejected" in statuses for statuses in outcomes.values()),
    )


def _by_resume_stats(
    first_applied: dict[int, aiosqlite.Row],
    outcomes: dict[int, set[str]],
) -> dict[str, ResumeVariantStats]:
    variants: dict[str, list[int]] = {}
    for job_id, row in first_applied.items():
        variants.setdefault(row["resume_variant_at_change"] or "unknown", []).append(
            job_id
        )
    result: dict[str, ResumeVariantStats] = {}
    for name, job_ids in sorted(variants.items()):
        matched = [outcomes[job_id] for job_id in job_ids if job_id in outcomes]
        result[name] = ResumeVariantStats(
            sent=len(job_ids),
            responses=len(matched),
            interviews=sum(
                bool(statuses & _INTERVIEW_STATUSES) for statuses in matched
            ),
            offers=sum("offer" in statuses for statuses in matched),
            rejections=sum("rejected" in statuses for statuses in matched),
        )
    return result


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    values.sort()
    midpoint = len(values) // 2
    if len(values) % 2:
        return float(values[midpoint])
    return (values[midpoint - 1] + values[midpoint]) / 2.0


def _empty_stats() -> ApplicationStats:
    return ApplicationStats(
        applied_count=0,
        response_count=0,
        interview_count=0,
        offer_count=0,
        rejection_count=0,
        median_days_to_response=None,
        by_resume=None,
    )
