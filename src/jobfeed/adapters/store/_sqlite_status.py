"""SQLite status management mixin — module-level async functions.

Each function receives an ``aiosqlite.Connection`` and an ``asyncio.Lock``
from the SQLiteStore facade.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._normalize import normalize_company
from jobfeed.domain.models import (
    AutoDecayResult,
    StatusInfo,
    WorkflowAttention,
    WorkflowAttentionItem,
)
from jobfeed.domain.status import DECAY_SOURCES, validate_transition

# -- helpers -----------------------------------------------------------------


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO-8601 timestamp from SQLite into a datetime.

    Args:
        value: ISO timestamp string.

    Returns:
        Parsed datetime (UTC, naive).
    """
    if value is None:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return datetime.fromisoformat(value)


def _status_info_from_row(row: aiosqlite.Row) -> StatusInfo:
    """Build a StatusInfo from a joined job_status + jobs row.

    Args:
        row: Database row with status and job columns.

    Returns:
        Populated StatusInfo.
    """
    return StatusInfo(
        job_id=str(row["job_id"]),
        status=row["status"],
        next_followup_at=(
            _parse_dt(row["next_followup_at"]) if row["next_followup_at"] else None
        ),
        resume_variant=row["resume_variant"],
        notes=row["notes"],
        last_status_change_at=_parse_dt(row["last_status_change_at"]),
    )


async def _transition_status_in_tx(
    db: aiosqlite.Connection,
    *,
    job_id: str,
    new_status: str,
    reason: str | None = None,
    resume_variant: str | None = None,
    force: bool = False,
    i_mean_it: bool = False,
    followup_grace_days: int = 7,
) -> str:
    """Write status transition inside an existing transaction (no lock/commit).

    Args:
        db: Active database connection (caller holds lock and transaction).
        job_id: Store-assigned job identity.
        new_status: Target status value.
        reason: Optional reason tag.
        resume_variant: Optional resume variant name.
        force: Bypass transition graph.
        i_mean_it: Required with force for archived to new.
        followup_grace_days: Days until next follow-up after applying.

    Returns:
        The new status string.

    Raises:
        ValueError: If the transition is invalid.
        KeyError: If the job has no status row.
    """
    cursor = await db.execute(
        "SELECT status FROM job_status WHERE job_id = ?", (int(job_id),)
    )
    row = await cursor.fetchone()
    if row is None:
        raise KeyError(f"no status row for job_id={job_id}")
    old_status: str = row["status"]

    err = validate_transition(old_status, new_status, force=force, i_mean_it=i_mean_it)
    if err is not None:
        raise ValueError(err)

    # Compute next_followup_at for applied transition
    followup_val: str | None = None
    if new_status == "applied":
        followup_dt = datetime.now(UTC) + timedelta(days=followup_grace_days)
        followup_val = followup_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    reason_tag = reason
    if force and reason_tag is None:
        reason_tag = "FORCE"

    await db.execute(
        """UPDATE job_status
           SET status = ?,
               next_followup_at = COALESCE(?, next_followup_at),
               resume_variant = COALESCE(?, resume_variant),
               last_status_change_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           WHERE job_id = ?""",
        (new_status, followup_val, resume_variant, int(job_id)),
    )
    await db.execute(
        """INSERT INTO job_status_history
               (job_id, from_status, to_status, reason, resume_variant_at_change)
           VALUES (?, ?, ?, ?, ?)""",
        (int(job_id), old_status, new_status, reason_tag, resume_variant),
    )
    return new_status


# -- public API --------------------------------------------------------------


async def transition_status(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    job_id: str,
    new_status: str,
    reason: str | None = None,
    resume_variant: str | None = None,
    force: bool = False,
    i_mean_it: bool = False,
    followup_grace_days: int = 7,
) -> str:
    """Transition a job's status with validation and history.

    Args:
        db: Database connection.
        lock: Write lock.
        job_id: Store-assigned job identity.
        new_status: Target status value.
        reason: Optional reason tag.
        resume_variant: Optional resume variant name.
        force: Bypass transition graph.
        i_mean_it: Required with force for archived to new.
        followup_grace_days: Days until next follow-up after applying.

    Returns:
        The new status string.
    """
    async with lock:
        result = await _transition_status_in_tx(
            db,
            job_id=job_id,
            new_status=new_status,
            reason=reason,
            resume_variant=resume_variant,
            force=force,
            i_mean_it=i_mean_it,
            followup_grace_days=followup_grace_days,
        )
        await db.commit()
        return result


async def get_status(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    job_id: str,
) -> StatusInfo | None:
    """Get current status for a job.

    Args:
        db: Database connection.
        lock: Write lock.
        job_id: Store-assigned job identity.

    Returns:
        Status info if found, else None.
    """
    async with lock:
        cursor = await db.execute(
            """SELECT s.job_id, s.status, s.next_followup_at,
                      s.resume_variant, s.notes, s.last_status_change_at
               FROM job_status s
               JOIN jobs j ON j.id = s.job_id
               WHERE s.job_id = ?""",
            (int(job_id),),
        )
        row = await cursor.fetchone()
    return _status_info_from_row(row) if row else None


async def restore_from_archived(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    job_id: str,
) -> str:
    """Restore an archived job to its most recent non-archived status.

    Args:
        db: Database connection.
        lock: Write lock.
        job_id: Store-assigned job identity.

    Returns:
        The restored status string.

    Raises:
        ValueError: If job is not archived or no prior status exists.
    """
    async with lock:
        # Find the most recent non-archived to_status in history
        cursor = await db.execute(
            """SELECT to_status FROM job_status_history
               WHERE job_id = ? AND to_status != 'archived'
               ORDER BY changed_at DESC, id DESC
               LIMIT 1""",
            (int(job_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"no non-archived history for job_id={job_id}")
        target_status: str = row["to_status"]

        result = await _transition_status_in_tx(
            db,
            job_id=job_id,
            new_status=target_status,
            reason="restore_from_archived",
            force=True,
            i_mean_it=True,
        )
        await db.commit()
        return result


async def auto_decay(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    ghost_days: int = 30,
    archive_ignored_days: int = 14,
) -> AutoDecayResult:
    """Sweep stale jobs to ghosted or archived.

    Args:
        db: Database connection.
        lock: Write lock.
        ghost_days: Days of silence before ghosting DECAY_SOURCES.
        archive_ignored_days: Days before archiving ignored jobs.

    Returns:
        Counts of ghosted and archived jobs.
    """
    decay_placeholders = ",".join("?" for _ in DECAY_SOURCES)
    decay_list = sorted(DECAY_SOURCES)

    async with lock:
        # Ghost stale DECAY_SOURCES
        cursor = await db.execute(
            f"""SELECT job_id FROM job_status
                WHERE status IN ({decay_placeholders})
                  AND last_status_change_at < datetime('now', ? || ' days')""",
            (*decay_list, f"-{ghost_days}"),
        )
        ghost_rows = list(await cursor.fetchall())
        ghost_count = len(ghost_rows)
        for row in ghost_rows:
            await _transition_status_in_tx(
                db,
                job_id=str(row["job_id"]),
                new_status="ghosted",
                reason="auto_decay",
                force=True,
            )

        # Archive stale ignored
        cursor = await db.execute(
            """SELECT job_id FROM job_status
               WHERE status = 'ignored'
                 AND last_status_change_at < datetime('now', ? || ' days')""",
            (f"-{archive_ignored_days}",),
        )
        archive_rows = list(await cursor.fetchall())
        archive_count = len(archive_rows)
        for row in archive_rows:
            await _transition_status_in_tx(
                db,
                job_id=str(row["job_id"]),
                new_status="archived",
                reason="auto_decay",
                force=True,
            )

        await db.commit()

    return AutoDecayResult(ghosted=ghost_count, archived=archive_count)


async def list_statuses(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    statuses: frozenset[str] | None = None,
    days: int | None = None,
    no_response_days: int | None = None,
    needs_followup: bool = False,
    notes_contain: str | None = None,
    limit: int | None = None,
) -> list[StatusInfo]:
    """Query jobs by status with optional filters.

    Args:
        db: Database connection.
        lock: Write lock.
        statuses: Restrict to these status values.
        days: Only changes within N days.
        no_response_days: Applied but silent for N days.
        needs_followup: Follow-up date in past or today.
        notes_contain: Substring match in notes.
        limit: Max results.

    Returns:
        Matching status info records.
    """
    clauses: list[str] = []
    params: list[object] = []

    if statuses:
        ph = ",".join("?" for _ in statuses)
        clauses.append(f"s.status IN ({ph})")
        params.extend(sorted(statuses))

    if days is not None:
        clauses.append("s.last_status_change_at >= datetime('now', ? || ' days')")
        params.append(f"-{days}")

    if no_response_days is not None:
        clauses.append("s.status = 'applied'")
        clauses.append("s.last_status_change_at < datetime('now', ? || ' days')")
        params.append(f"-{no_response_days}")

    if needs_followup:
        clauses.append("s.next_followup_at IS NOT NULL")
        clauses.append("date(s.next_followup_at) <= date('now')")

    if notes_contain:
        clauses.append("s.notes LIKE '%' || ? || '%'")
        params.append(notes_contain)

    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"""SELECT s.job_id, s.status, s.next_followup_at,
                     s.resume_variant, s.notes, s.last_status_change_at
              FROM job_status s
              JOIN jobs j ON j.id = s.job_id
              WHERE {where}
              ORDER BY s.last_status_change_at DESC"""

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    async with lock:
        cursor = await db.execute(sql, tuple(params))
        rows = await cursor.fetchall()

    return [_status_info_from_row(r) for r in rows]


async def append_note(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    job_id: str,
    text: str,
) -> None:
    """Append a timestamped note and reset the ghost clock.

    Args:
        db: Database connection.
        lock: Write lock.
        job_id: Store-assigned job identity.
        text: Note text to append.
    """
    prefix = datetime.now().strftime("[%Y-%m-%d %H:%M] ")
    line = prefix + text + "\n"
    async with lock:
        await db.execute(
            """UPDATE job_status
               SET notes = COALESCE(notes, '') || ?,
                   last_status_change_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE job_id = ?""",
            (line, int(job_id)),
        )
        await db.commit()


def _attention_item(row: aiosqlite.Row, reason: str) -> WorkflowAttentionItem:
    """Build a WorkflowAttentionItem from a joined query row.

    Args:
        row: Database row with job and status columns.
        reason: Bucket reason label.

    Returns:
        Populated attention item.
    """
    return WorkflowAttentionItem(
        job_id=str(row["job_id"]),
        title=row["title"],
        company=row["company"],
        url=row["url"],
        status=row["status"],
        last_status_change_at=_parse_dt(row["last_status_change_at"]),
        next_followup_at=(
            _parse_dt(row["next_followup_at"]) if row["next_followup_at"] else None
        ),
        notes=row["notes"],
        reason=reason,
        days_since=row["days_since"],
    )


_ATTENTION_COLS = """s.job_id, j.title, j.company, j.url,
    s.status, s.last_status_change_at, s.next_followup_at, s.notes"""


async def workflow_attention(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    auto_ghost_days: int = 30,
    lookahead_days: int = 5,
) -> WorkflowAttention:
    """Three-bucket workflow attention report.

    Args:
        db: Database connection.
        lock: Write lock.
        auto_ghost_days: Ghost threshold in days.
        lookahead_days: Early warning window in days.

    Returns:
        Follow-up, interview prep, and going-ghosted lists.
    """
    async with lock:
        # Bucket 1: follow_up_today
        cur = await db.execute(
            f"""SELECT {_ATTENTION_COLS},
                   CAST(julianday('now') - julianday(s.next_followup_at) AS INTEGER)
                       AS days_since
               FROM job_status s JOIN jobs j ON j.id = s.job_id
               WHERE s.status IN ('applied','interviewing')
                 AND s.next_followup_at IS NOT NULL
                 AND date(s.next_followup_at) <= date('now','localtime')
               ORDER BY s.next_followup_at ASC""",
        )
        follow_up = [_attention_item(r, "follow-up due") for r in await cur.fetchall()]

        # Bucket 2: interview_prep
        cur = await db.execute(
            f"""SELECT {_ATTENTION_COLS},
                   CAST(julianday('now') - julianday(s.last_status_change_at)
                        AS INTEGER) AS days_since
               FROM job_status s JOIN jobs j ON j.id = s.job_id
               WHERE s.status = 'interviewing'
               ORDER BY s.last_status_change_at DESC""",
        )
        interview = [_attention_item(r, "interview prep") for r in await cur.fetchall()]

        # Bucket 3: going_ghosted
        warn_days = auto_ghost_days - lookahead_days
        cur = await db.execute(
            f"""SELECT {_ATTENTION_COLS},
                   CAST(julianday('now') - julianday(s.last_status_change_at)
                        AS INTEGER) AS days_since
               FROM job_status s JOIN jobs j ON j.id = s.job_id
               WHERE s.status IN ('applied','interviewing')
                 AND s.last_status_change_at < datetime('now', ? || ' days')
               ORDER BY s.last_status_change_at ASC""",
            (f"-{warn_days}",),
        )
        ghosting = [_attention_item(r, "going silent") for r in await cur.fetchall()]

    return WorkflowAttention(
        follow_up_today=follow_up,
        interview_prep=interview,
        going_ghosted=ghosting,
    )


async def compute_reapply_notice(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    job_id: str,
    lookback_days: int = 60,
) -> str | None:
    """Detect same-company active application for reapply warning.

    Args:
        db: Database connection.
        lock: Write lock.
        job_id: Job to check for company overlap.
        lookback_days: How far back to look for active applications.

    Returns:
        Notice string if an active same-company application exists, else None.
    """
    async with lock:
        # Get this job's company_norm
        cursor = await db.execute(
            "SELECT company, company_norm FROM jobs WHERE id = ?", (int(job_id),)
        )
        job_row = await cursor.fetchone()
        if job_row is None:
            return None

        company_norm = job_row["company_norm"] or normalize_company(job_row["company"])
        if not company_norm:
            return None

        # Find active applications at same company (excluding this job)
        cursor = await db.execute(
            """SELECT j.id, j.title, s.status
               FROM jobs j
               JOIN job_status s ON s.job_id = j.id
               WHERE (j.company_norm = ? OR ? = '')
                 AND j.id != ?
                 AND s.status = 'applied'
                 AND s.last_status_change_at >= datetime('now', ? || ' days')
               LIMIT 1""",
            (company_norm, company_norm, int(job_id), f"-{lookback_days}"),
        )
        match = await cursor.fetchone()

    if match is None:
        return None
    return (
        f"Active application at same company: "
        f"'{match['title']}' (job {match['id']}, status={match['status']})"
    )
