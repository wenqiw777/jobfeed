"""Async SQLite implementations for application audit trail and resume snapshots.

Module-level functions (not class methods) that take ``db`` and ``lock`` as the
first two positional parameters.  Every SQL write acquires the lock and commits
inside it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.domain.models_application import (
    ApplicationRecord,
    ApplicationStats,
    ResumeSnapshot,
    ResumeVariantStats,
)
from jobfeed.domain.status import RESPONSE_STATUSES

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_INTERVIEW_STATUSES = {"interviewing", "offer"}


async def _transition_in_tx(
    db: aiosqlite.Connection,
    *,
    job_id: int,
    new_status: str,
    reason: str | None = None,
    resume_variant: str | None = None,
) -> None:
    """Record a status transition inside an already-open transaction.

    This is intentionally *not* the public ``transition_status`` method
    -- it does not acquire the lock or start/commit a transaction
    itself.  The caller is responsible for both.

    Args:
        db: Open aiosqlite connection (transaction already active).
        job_id: Integer job row id.
        new_status: Target status value.
        reason: Optional reason tag for the history row.
        resume_variant: Optional resume variant name to record.
    """
    now = datetime.now(UTC).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ",
    )

    # Fetch current status
    cursor = await db.execute(
        "SELECT status FROM job_status WHERE job_id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    from_status: str | None = row[0] if row else None

    # Update current status row
    await db.execute(
        """
        UPDATE job_status
           SET status = ?,
               last_status_change_at = ?,
               resume_variant = COALESCE(?, resume_variant)
         WHERE job_id = ?
        """,
        (new_status, now, resume_variant, job_id),
    )

    # Append to history log
    await db.execute(
        """
        INSERT INTO job_status_history
            (job_id, from_status, to_status, changed_at,
             reason, resume_variant_at_change)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, from_status, new_status, now, reason, resume_variant),
    )


def _median(values: list[int]) -> float | None:
    """Compute the median of an integer list.

    Args:
        values: Integer list (will be sorted in-place).

    Returns:
        Median as float, or None if the list is empty.
    """
    if not values:
        return None
    values.sort()
    n = len(values)
    if n % 2 == 1:
        return float(values[n // 2])
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def _count_outcomes(
    job_response_statuses: dict[int, set[str]],
) -> tuple[int, int, int, int]:
    """Count response, interview, offer, and rejection totals.

    Args:
        job_response_statuses: Mapping of job_id to sets of reached
            response statuses.

    Returns:
        Tuple of (response_count, interview_count, offer_count,
        rejection_count).
    """
    response_count = len(job_response_statuses)
    interview_count = sum(
        1
        for s in job_response_statuses.values()
        if s & _INTERVIEW_STATUSES
    )
    offer_count = sum(
        1 for s in job_response_statuses.values() if "offer" in s
    )
    rejection_count = sum(
        1
        for s in job_response_statuses.values()
        if "rejected" in s
    )
    return (
        response_count,
        interview_count,
        offer_count,
        rejection_count,
    )


async def _fetch_response_deltas(
    db: aiosqlite.Connection,
    job_ids: set[int],
) -> list[int]:
    """Compute days-to-first-response for each applied job.

    Args:
        db: Open aiosqlite connection (caller holds the lock).
        job_ids: Job IDs that received at least one response.

    Returns:
        List of whole-day deltas (unsorted).
    """
    sorted_resp = sorted(RESPONSE_STATUSES)
    resp_ph = ",".join("?" for _ in sorted_resp)
    deltas: list[int] = []

    for jid in job_ids:
        cursor = await db.execute(
            """
            SELECT changed_at
              FROM job_status_history
             WHERE job_id = ? AND to_status = 'applied'
             ORDER BY changed_at ASC
             LIMIT 1
            """,
            (jid,),
        )
        applied_row = await cursor.fetchone()
        if applied_row is None:
            continue

        cursor = await db.execute(
            f"""
            SELECT changed_at
              FROM job_status_history
             WHERE job_id = ?
               AND to_status IN ({resp_ph})
             ORDER BY changed_at ASC
             LIMIT 1
            """,
            (jid, *sorted_resp),
        )
        resp_row = await cursor.fetchone()
        if resp_row is None:
            continue

        applied_dt = datetime.fromisoformat(applied_row[0])
        resp_dt = datetime.fromisoformat(resp_row[0])
        deltas.append((resp_dt - applied_dt).days)

    return deltas


def _build_variant_stats(
    variant_rows: list[tuple[int, str | None]],
    job_resp: dict[int, set[str]],
) -> dict[str, ResumeVariantStats]:
    """Build per-resume-variant outcome breakdown.

    Args:
        variant_rows: (job_id, resume_variant_at_change) pairs.
        job_resp: Mapping of job_id to sets of reached response
            statuses.

    Returns:
        Mapping of variant name to stats.
    """
    job_variant: dict[int, str] = {}
    for jid, raw_name in variant_rows:
        job_variant[jid] = (
            raw_name if raw_name is not None else "unknown"
        )

    variant_jobs: dict[str, set[int]] = {}
    for jid, vname in job_variant.items():
        variant_jobs.setdefault(vname, set()).add(jid)

    result: dict[str, ResumeVariantStats] = {}
    for vname, jids in sorted(variant_jobs.items()):
        v_resp = {
            j: job_resp[j] for j in jids if j in job_resp
        }
        result[vname] = ResumeVariantStats(
            sent=len(jids),
            responses=len(v_resp),
            interviews=sum(
                1
                for s in v_resp.values()
                if s & _INTERVIEW_STATUSES
            ),
            offers=sum(
                1 for s in v_resp.values() if "offer" in s
            ),
            rejections=sum(
                1
                for s in v_resp.values()
                if "rejected" in s
            ),
        )
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def record_application(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    record: ApplicationRecord,
) -> bool:
    """Record a job application with an atomic status transition.

    Inserts into the ``applied`` table and, if the row is new,
    transitions the job status to ``'applied'`` inside the same SQLite
    transaction.

    Args:
        db: Open aiosqlite connection.
        lock: Shared write lock.
        record: Application audit record to persist.

    Returns:
        True if the application was newly recorded, False if the job
        was already marked as applied.
    """
    job_id_int = int(record.job_id)
    applied_at_str = record.applied_at.isoformat()

    async with lock:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO applied
                    (job_id, applied_at, notes,
                     master_resume_hash,
                     tailored_resume_hash, cover_letter,
                     application_method,
                     verdict_snapshot, fit_snapshot,
                     hooks_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id_int,
                    applied_at_str,
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

            if cursor.rowcount == 0:
                await db.commit()
                return False

            await _transition_in_tx(
                db,
                job_id=job_id_int,
                new_status="applied",
                reason="record_application",
                resume_variant=None,
            )

            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return True


async def list_applications(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    limit: int = 100,
) -> list[ApplicationRecord]:
    """List application records by recency.

    Args:
        db: Open aiosqlite connection.
        lock: Shared write lock (acquired for read consistency).
        limit: Maximum number of records to return.

    Returns:
        Application records ordered by ``applied_at`` descending.
    """
    async with lock:
        cursor = await db.execute(
            """
            SELECT a.job_id,
                   a.applied_at,
                   a.master_resume_hash,
                   a.tailored_resume_hash,
                   a.cover_letter,
                   a.application_method,
                   a.verdict_snapshot,
                   a.fit_snapshot,
                   a.hooks_snapshot,
                   a.notes
              FROM applied a
              JOIN jobs j ON a.job_id = j.id
             ORDER BY a.applied_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

    results: list[ApplicationRecord] = []
    for row in rows:
        results.append(
            ApplicationRecord(
                job_id=str(row[0]),
                applied_at=datetime.fromisoformat(row[1]),
                master_resume_hash=row[2],
                tailored_resume_hash=row[3],
                cover_letter=row[4],
                application_method=row[5],
                verdict_snapshot=row[6],
                fit_snapshot=row[7],
                hooks_snapshot=row[8],
                notes=row[9],
            )
        )
    return results


async def application_stats(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    since_days_ago: int = 30,
    by_resume: bool = False,
) -> ApplicationStats:
    """Compute aggregate application statistics over a time window.

    The window is defined as ``since_days_ago`` days before the
    current UTC time.  Counts are derived from
    ``job_status_history`` rows.

    Args:
        db: Open aiosqlite connection.
        lock: Shared write lock (acquired for read consistency).
        since_days_ago: Number of days to look back.
        by_resume: If True, include per-variant breakdown.

    Returns:
        Aggregate application statistics.
    """
    cutoff = (
        datetime.now(UTC) - timedelta(days=since_days_ago)
    ).isoformat()

    _empty = ApplicationStats(
        applied_count=0,
        response_count=0,
        interview_count=0,
        offer_count=0,
        rejection_count=0,
        median_days_to_response=None,
        by_resume=None,
    )

    async with lock:
        # 1. Applied jobs in window
        cursor = await db.execute(
            """
            SELECT DISTINCT job_id
              FROM job_status_history
             WHERE to_status = 'applied'
               AND changed_at >= ?
            """,
            (cutoff,),
        )
        applied_rows = await cursor.fetchall()
        applied_job_ids = {row[0] for row in applied_rows}

        if not applied_job_ids:
            return _empty

        ph = ",".join("?" for _ in applied_job_ids)
        id_list = list(applied_job_ids)
        resp_ph = ",".join("?" for _ in RESPONSE_STATUSES)

        # 2. Response statuses reached
        cursor = await db.execute(
            f"""
            SELECT DISTINCT job_id, to_status
              FROM job_status_history
             WHERE job_id IN ({ph})
               AND to_status IN ({resp_ph})
            """,
            (*id_list, *sorted(RESPONSE_STATUSES)),
        )
        response_rows = await cursor.fetchall()

        job_resp: dict[int, set[str]] = {}
        for row in response_rows:
            job_resp.setdefault(row[0], set()).add(row[1])

        resp_ct, int_ct, off_ct, rej_ct = _count_outcomes(
            job_resp,
        )

        # 3. Median days to first response
        deltas = await _fetch_response_deltas(
            db, set(job_resp.keys()),
        )
        median_days = _median(deltas)

        # 4. Per-variant breakdown (optional)
        by_resume_dict: (
            dict[str, ResumeVariantStats] | None
        ) = None
        if by_resume:
            cursor = await db.execute(
                f"""
                SELECT job_id, resume_variant_at_change
                  FROM job_status_history
                 WHERE job_id IN ({ph})
                   AND to_status = 'applied'
                   AND changed_at >= ?
                """,
                (*id_list, cutoff),
            )
            vrows = await cursor.fetchall()
            by_resume_dict = _build_variant_stats(
                [(r[0], r[1]) for r in vrows],
                job_resp,
            )

    return ApplicationStats(
        applied_count=len(applied_job_ids),
        response_count=resp_ct,
        interview_count=int_ct,
        offer_count=off_ct,
        rejection_count=rej_ct,
        median_days_to_response=median_days,
        by_resume=by_resume_dict,
    )


async def save_resume_snapshot(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    snapshot: ResumeSnapshot,
) -> None:
    """Persist a content-addressed resume snapshot (no-op if exists).

    Args:
        db: Open aiosqlite connection.
        lock: Shared write lock.
        snapshot: Resume snapshot to store.
    """
    async with lock:
        await db.execute(
            """
            INSERT OR IGNORE INTO resume_snapshots
                (resume_hash, captured_at, source,
                 content, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot.resume_hash,
                snapshot.captured_at.isoformat(),
                snapshot.source,
                snapshot.content,
                snapshot.notes,
            ),
        )
        await db.commit()


async def get_resume_snapshot(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    resume_hash: str,
) -> ResumeSnapshot | None:
    """Load a resume snapshot by its content hash.

    Args:
        db: Open aiosqlite connection.
        lock: Shared write lock (acquired for read consistency).
        resume_hash: SHA-256 hex digest to look up.

    Returns:
        The matching snapshot, or None if not found.
    """
    async with lock:
        cursor = await db.execute(
            """
            SELECT resume_hash, captured_at, source,
                   content, notes
              FROM resume_snapshots
             WHERE resume_hash = ?
            """,
            (resume_hash,),
        )
        row = await cursor.fetchone()

    if row is None:
        return None

    return ResumeSnapshot(
        resume_hash=row[0],
        captured_at=datetime.fromisoformat(row[1]),
        source=row[2],
        content=row[3],
        notes=row[4],
    )


async def register_resume_variant(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    name: str,
    description: str | None = None,
) -> bool:
    """Register a named resume variant for A/B tracking.

    Args:
        db: Open aiosqlite connection.
        lock: Shared write lock.
        name: Variant name (primary key).
        description: Optional human-readable description.

    Returns:
        True if the variant was newly created, False if it existed.
    """
    async with lock:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO resume_variants
                (name, description)
            VALUES (?, ?)
            """,
            (name, description),
        )
        await db.commit()

    return cursor.rowcount == 1
