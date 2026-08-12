"""SQLite status aggregate commands and workflow projections."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import aiosqlite

from jobfeed.adapters.store._normalize import normalize_company
from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _immediate_transaction,
    _parse_utc_timestamp,
    _placeholders,
    _require_utc_timestamp,
)
from jobfeed.domain.models import (
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    JobStatus,
    StatusFilter,
    StatusInfo,
    TransitionRequest,
    WorkflowAttention,
    WorkflowAttentionItem,
)
from jobfeed.domain.status import (
    ACTIVE_APPLICATION_STATUSES,
    DECAY_SOURCES,
    is_terminal,
    validate_transition,
)

if TYPE_CHECKING:
    from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle

_STATUS_COLUMNS = """s.job_id, s.status, s.next_followup_at, s.resume_variant,
    s.notes, s.last_status_change_at, j.company, j.title"""
_ATTENTION_COLUMNS = """s.job_id, j.title, j.company, j.url, s.status,
    s.last_status_change_at, s.next_followup_at, s.notes"""


class _SqliteStatus:
    """Implement status lifecycle operations over an injected lifecycle."""

    _lifecycle: SqliteLifecycle

    def _application_time(self, value: datetime | None = None) -> datetime:
        raise NotImplementedError

    async def transition_status(self, request: TransitionRequest) -> str:
        """Atomically update current status and append exactly one history row."""
        now = self._application_time()
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            return await self._transition_status_in_tx(connection, request, now=now)

    async def get_status(self, job_id: str) -> StatusInfo | None:
        """Load the current joined status projection for one decimal job id."""
        async with self._lifecycle.connection() as connection:
            row = await _fetch_row(
                connection,
                f"""SELECT {_STATUS_COLUMNS} FROM job_status s
                    JOIN jobs j ON j.id=s.job_id WHERE s.job_id=?""",
                (int(job_id),),
            )
        return _status_info(row) if row is not None else None

    async def get_status_history(self, job_id: str) -> list[str]:
        """Return status destinations in append-only identity order newest first."""
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                "SELECT to_status FROM job_status_history "
                "WHERE job_id=? ORDER BY id DESC",
                (int(job_id),),
            )
        return [str(row["to_status"]) for row in rows]

    async def list_statuses(
        self,
        filters: StatusFilter | None = None,
    ) -> list[StatusInfo]:
        """Query joined statuses with the frozen AND-composed filters."""
        query = filters or StatusFilter()
        now = self._application_time()
        clauses: list[str] = []
        params: list[object] = []
        if query.statuses:
            statuses = sorted(query.statuses)
            clauses.append(f"s.status IN ({_placeholders(statuses)})")
            params.extend(statuses)
        if query.days is not None:
            clauses.append("s.last_status_change_at>=?")
            params.append(_require_utc_timestamp(now - timedelta(days=query.days)))
        if query.since is not None:
            clauses.append("s.last_status_change_at>=?")
            params.append(_require_utc_timestamp(self._application_time(query.since)))
        if query.no_response_days is not None:
            clauses.extend(
                [
                    "s.status IN ('applied','interviewing')",
                    "s.last_status_change_at<?",
                ]
            )
            params.append(
                _require_utc_timestamp(
                    now - timedelta(days=query.no_response_days)
                )
            )
        if query.needs_followup:
            clauses.extend(
                [
                    "s.next_followup_at IS NOT NULL",
                    "date(s.next_followup_at)<=date(?)",
                ]
            )
            params.append(_require_utc_timestamp(now))
        if query.notes_contain:
            clauses.append("unicode_casefold(s.notes) LIKE unicode_casefold(?)")
            params.append(f"%{query.notes_contain}%")
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = (
            f"SELECT {_STATUS_COLUMNS} FROM job_status s "
            f"JOIN jobs j ON j.id=s.job_id WHERE {where} "
            "ORDER BY s.last_status_change_at DESC"
        )
        if query.limit is not None:
            if query.limit < 0:
                raise ValueError("limit must be nonnegative")
            sql += " LIMIT ?"
            params.append(query.limit)
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(connection, sql, params)
        return [_status_info(row) for row in rows]

    async def append_note(self, *, job_id: str, text: str) -> bool:
        """Append a UTC-minute note line and reset the status activity clock."""
        now = self._application_time()
        line = now.strftime("[%Y-%m-%d %H:%M] ") + text + "\n"
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "UPDATE job_status SET notes=COALESCE(notes,'')||?, "
                "last_status_change_at=? WHERE job_id=?",
                (line, _require_utc_timestamp(now), int(job_id)),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed == 1

    async def set_followup(self, *, job_id: str, at: datetime) -> bool:
        """Set an exact aware follow-up without changing status activity time."""
        timestamp = _require_utc_timestamp(self._application_time(at), "at")
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "UPDATE job_status SET next_followup_at=? WHERE job_id=?",
                (timestamp, int(job_id)),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed == 1

    async def auto_decay(
        self,
        *,
        ghost_days: int = 30,
        archive_ignored_days: int = 14,
    ) -> AutoDecayResult:
        """Atomically ghost and archive every strictly stale eligible status."""
        now = self._application_time()
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            ghost_rows = await _fetch_rows(
                connection,
                f"SELECT job_id FROM job_status WHERE status IN "
                f"({_placeholders(DECAY_SOURCES)}) AND last_status_change_at<?",
                (
                    *sorted(DECAY_SOURCES),
                    _require_utc_timestamp(now - timedelta(days=ghost_days)),
                ),
            )
            archive_rows = await _fetch_rows(
                connection,
                "SELECT job_id FROM job_status WHERE status='ignored' "
                "AND last_status_change_at<?",
                (
                    _require_utc_timestamp(
                        now - timedelta(days=archive_ignored_days)
                    ),
                ),
            )
            for row in ghost_rows:
                await self._transition_status_in_tx(
                    connection,
                    TransitionRequest(
                        job_id=str(row["job_id"]),
                        new_status="ghosted",
                        reason="auto_decay",
                        force=True,
                    ),
                    now=now,
                )
            for row in archive_rows:
                await self._transition_status_in_tx(
                    connection,
                    TransitionRequest(
                        job_id=str(row["job_id"]),
                        new_status="archived",
                        reason="auto_decay",
                        force=True,
                    ),
                    now=now,
                )
        return AutoDecayResult(ghosted=len(ghost_rows), archived=len(archive_rows))

    async def compute_reapply_notice(
        self,
        *,
        job_id: str,
        lookback_days: int = 60,
    ) -> str | None:
        """Return the frozen same-company active-application notice when present."""
        numeric_id = int(job_id)
        async with self._lifecycle.connection() as connection:
            job = await _fetch_row(
                connection,
                "SELECT company, company_norm FROM jobs WHERE id=?",
                (numeric_id,),
            )
            if job is None:
                return None
            company_norm = job["company_norm"] or normalize_company(str(job["company"]))
            if not company_norm:
                return None
            statuses = sorted(ACTIVE_APPLICATION_STATUSES)
            match = await _fetch_row(
                connection,
                f"""SELECT j.id,j.title,s.status FROM jobs j
                    JOIN job_status s ON s.job_id=j.id
                    WHERE j.company_norm=? AND j.id!=?
                      AND s.status IN ({_placeholders(statuses)})
                      AND s.last_status_change_at>=? LIMIT 1""",
                (
                    company_norm,
                    numeric_id,
                    *statuses,
                    _require_utc_timestamp(
                        self._application_time() - timedelta(days=lookback_days)
                    ),
                ),
            )
        if match is None:
            return None
        return (
            "Active application at same company: "
            f"'{match['title']}' (job {match['id']}, status={match['status']})"
        )

    async def expand_twin_ids(self, job_ids: list[int]) -> dict[int, list[int]]:
        """Expand job ids through nonblank normalized company and title keys."""
        result: dict[int, list[int]] = {}
        async with self._lifecycle.connection() as connection:
            for job_id in job_ids:
                row = await _fetch_row(
                    connection,
                    "SELECT company_norm,title_norm FROM jobs WHERE id=?",
                    (job_id,),
                )
                if row is None or not row["company_norm"] or not row["title_norm"]:
                    result[job_id] = [job_id]
                    continue
                twins = await _fetch_rows(
                    connection,
                    "SELECT id FROM jobs WHERE company_norm=? AND title_norm=?",
                    (row["company_norm"], row["title_norm"]),
                )
                result[job_id] = [int(twin["id"]) for twin in twins]
        return result

    async def transition_status_bulk(
        self,
        request: BulkTransitionRequest,
    ) -> BulkResult:
        """Transition each twin cluster atomically while isolating cluster errors."""
        result = BulkResult()
        twin_map = await self.expand_twin_ids([int(item[0]) for item in request.items])
        processed: set[int] = set()
        for selected_id, target in request.items:
            numeric_id = int(selected_id)
            if numeric_id in processed:
                continue
            cluster = twin_map.get(numeric_id, [numeric_id])
            try:
                counts = await self._transition_cluster(
                    selected_id,
                    target,
                    cluster,
                    request,
                )
            except Exception as error:
                result.failed.append((selected_id, str(error)))
                continue
            succeeded, skipped, cascaded = counts
            result.succeeded += succeeded
            result.skipped += skipped
            result.cascaded += cascaded
            processed.update(cluster)
        return result

    async def workflow_attention(
        self,
        *,
        auto_ghost_days: int = 30,
        lookahead_days: int = 5,
    ) -> WorkflowAttention:
        """Build the three weakly consistent workflow attention buckets."""
        now = self._application_time()
        follow = await self._attention_rows(
            "s.status IN ('applied','interviewing') "
            "AND s.next_followup_at IS NOT NULL AND date(s.next_followup_at)<=date(?)",
            (_require_utc_timestamp(now),),
            "s.next_followup_at ASC",
            "follow-up due",
            now,
        )
        interview = await self._interview_attention(now, lookahead_days)
        statuses = sorted(DECAY_SOURCES)
        ghosting = await self._attention_rows(
            f"s.status IN ({_placeholders(statuses)}) "
            "AND s.last_status_change_at<?",
            (
                *statuses,
                _require_utc_timestamp(
                    now - timedelta(days=auto_ghost_days - lookahead_days)
                ),
            ),
            "s.last_status_change_at ASC",
            "going silent",
            now,
        )
        return WorkflowAttention(
            follow_up_today=follow,
            interview_prep=interview,
            going_ghosted=ghosting,
        )

    async def _transition_status_in_tx(
        self,
        connection: aiosqlite.Connection,
        request: TransitionRequest,
        *,
        now: datetime,
    ) -> str:
        row = await _fetch_row(
            connection,
            "SELECT status FROM job_status WHERE job_id=?",
            (int(request.job_id),),
        )
        if row is None:
            raise KeyError(f"no status row for job_id={request.job_id}")
        old_status = str(row["status"])
        error = validate_transition(
            old_status,
            request.new_status,
            force=request.force,
            i_mean_it=request.i_mean_it,
        )
        if error is not None:
            raise ValueError(error)
        followup = None
        should_set_followup = request.new_status not in ACTIVE_APPLICATION_STATUSES
        if request.new_status == "applied":
            should_set_followup = True
            followup = now + timedelta(days=request.followup_grace_days)
        reason = "FORCE" if request.force and request.reason is None else request.reason
        await connection.execute(
            """UPDATE job_status SET status=?,
               next_followup_at=CASE WHEN ? THEN ? ELSE next_followup_at END,
               resume_variant=COALESCE(?,resume_variant),last_status_change_at=?
               WHERE job_id=?""",
            (
                request.new_status,
                should_set_followup,
                _require_utc_timestamp(followup) if followup else None,
                request.resume_variant,
                _require_utc_timestamp(now),
                int(request.job_id),
            ),
        )
        await self._after_status_update(connection)
        await connection.execute(
            """INSERT INTO job_status_history
               (job_id,from_status,to_status,changed_at,reason,resume_variant_at_change)
               VALUES (?,?,?,?,?,?)""",
            (
                int(request.job_id),
                old_status,
                request.new_status,
                _require_utc_timestamp(now),
                reason,
                request.resume_variant,
            ),
        )
        return request.new_status

    async def _transition_cluster(
        self,
        selected_id: str,
        target: str,
        cluster: list[int],
        request: BulkTransitionRequest,
    ) -> tuple[int, int, int]:
        now = self._application_time()
        succeeded = skipped = cascaded = 0
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            selected = int(selected_id)
            members = [selected, *(item for item in cluster if item != selected)]
            for member in members:
                await self._before_bulk_member(member, connection)
                row = await _fetch_row(
                    connection,
                    "SELECT status FROM job_status WHERE job_id=?",
                    (member,),
                )
                if member != int(selected_id) and (
                    row is None or is_terminal(str(row["status"]))
                ):
                    skipped += int(row is not None)
                    continue
                await self._transition_status_in_tx(
                    connection,
                    TransitionRequest(
                        job_id=str(member),
                        new_status=target,
                        reason=(
                            request.reason_selected
                            if member == int(selected_id)
                            else request.reason_cascade
                        ),
                        force=request.force,
                        i_mean_it=request.i_mean_it,
                    ),
                    now=now,
                )
                succeeded += 1
                cascaded += int(member != int(selected_id))
        return succeeded, skipped, cascaded

    async def _attention_rows(
        self,
        where: str,
        params: tuple[object, ...],
        order: str,
        reason: str,
        now: datetime,
    ) -> list[WorkflowAttentionItem]:
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                f"SELECT {_ATTENTION_COLUMNS} FROM job_status s "
                f"JOIN jobs j ON j.id=s.job_id WHERE {where} ORDER BY {order}",
                params,
            )
        return [_attention_item(row, reason, now) for row in rows]

    async def _interview_attention(
        self,
        now: datetime,
        lookahead_days: int,
    ) -> list[WorkflowAttentionItem]:
        cutoff = _require_utc_timestamp(now + timedelta(days=lookahead_days))
        async with self._lifecycle.connection() as connection:
            scheduled = await _fetch_rows(
                connection,
                f"""SELECT {_ATTENTION_COLUMNS},MIN(ir.scheduled_at) AS round_time
                    FROM job_status s JOIN jobs j ON j.id=s.job_id
                    JOIN interview_rounds ir ON ir.job_id=s.job_id
                    WHERE s.status='interviewing' AND ir.completed_at IS NULL
                      AND ir.scheduled_at IS NOT NULL AND ir.scheduled_at<=?
                    GROUP BY s.job_id ORDER BY s.job_id,round_time ASC""",
                (cutoff,),
            )
            unscheduled = await _fetch_rows(
                connection,
                f"""SELECT {_ATTENTION_COLUMNS} FROM job_status s
                    JOIN jobs j ON j.id=s.job_id WHERE s.status='interviewing'
                    AND NOT EXISTS (SELECT 1 FROM interview_rounds ir
                      WHERE ir.job_id=s.job_id AND ir.completed_at IS NULL
                        AND ir.scheduled_at IS NOT NULL)
                    ORDER BY s.last_status_change_at DESC""",
            )
        return [
            *(_attention_item(row, "interview prep", now) for row in scheduled),
            *(
                _attention_item(row, "interviewing, unscheduled", now)
                for row in unscheduled
            ),
        ]

    async def _after_status_update(self, connection: aiosqlite.Connection) -> None:
        del connection

    async def _before_bulk_member(
        self,
        job_id: int,
        connection: aiosqlite.Connection,
    ) -> None:
        del job_id, connection


def _status_info(row: aiosqlite.Row) -> StatusInfo:
    return StatusInfo(
        job_id=str(row["job_id"]),
        status=JobStatus(str(row["status"])),
        next_followup_at=(
            _parse_utc_timestamp(row["next_followup_at"])
            if row["next_followup_at"] is not None
            else None
        ),
        resume_variant=row["resume_variant"],
        notes=row["notes"],
        last_status_change_at=_parse_utc_timestamp(row["last_status_change_at"]),
        company=row["company"],
        title=row["title"],
    )


def _attention_item(
    row: aiosqlite.Row,
    reason: str,
    now: datetime,
) -> WorkflowAttentionItem:
    changed_at = _parse_utc_timestamp(row["last_status_change_at"])
    followup = (
        _parse_utc_timestamp(row["next_followup_at"])
        if row["next_followup_at"] is not None
        else None
    )
    baseline = followup if reason == "follow-up due" and followup else changed_at
    return WorkflowAttentionItem(
        job_id=str(row["job_id"]),
        title=str(row["title"]),
        company=str(row["company"]),
        url=str(row["url"]),
        status=str(row["status"]),
        last_status_change_at=changed_at,
        next_followup_at=followup,
        notes=row["notes"],
        reason=reason,
        days_since=(now - baseline).days,
    )
