"""Canonical workflow commands. Source status rows remain immutable audit input."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import aiosqlite

from jobfeed.adapters.store._normalize import normalize_company
from jobfeed.adapters.store._sqlite_application import (
    _by_resume_stats,
    _count_outcomes,
    _empty_stats,
    _first_apply_by_job,
    _median,
    _register_variant,
    _save_snapshot,
)
from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _immediate_transaction,
    _parse_utc_timestamp,
    _placeholders,
    _require_utc_timestamp,
)
from jobfeed.adapters.store._sqlite_status import _attention_item
from jobfeed.adapters.store.sqlite_schema import _backfill_real_job_workflow
from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models_application import (
    ApplicationRecord,
    ApplicationStats,
    RealJobApplicationEvent,
    ResumeSnapshot,
)
from jobfeed.domain.models_status import (
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    StatusFilter,
    StatusInfo,
    TransitionRequest,
    WorkflowAttention,
)
from jobfeed.domain.status import (
    ACTIVE_APPLICATION_STATUSES,
    RESPONSE_STATUSES,
    is_terminal,
    pick_restore_target,
    validate_transition,
)

if TYPE_CHECKING:
    from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class SqliteRealJobWorkflow:
    """SQLite canonical workflow commands with transactional source provenance."""

    _lifecycle: SqliteLifecycle

    def _application_time(self, value: datetime | None = None) -> datetime:
        raise NotImplementedError

    async def backfill_real_job_workflow(self) -> None:
        """Repeat the explicit copy after source-status changes or alias joins."""
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            await _backfill_real_job_workflow(db)

    async def record_real_job_application_with_snapshots(  # noqa: PLR0913
        self,
        record: ApplicationRecord,
        *,
        real_job_id: str,
        source_job_id: str,
        apply_url: str | None,
        snapshots: list[ResumeSnapshot] | None = None,
        resume_variant: str | None = None,
    ) -> bool:
        """Record one submission and canonical decision in the same transaction.

        Args:
            record: Submitted application data to persist.
            real_job_id: Canonical parent ID, never a source posting ID.
            source_job_id: Source posting ID that supplied this event or lookup.
            apply_url: Observed application URL, if known.
            snapshots: Existing resume snapshots to retain for source audit.
            resume_variant: Optional resume variant saved on the canonical decision.

        Returns:
            True for a newly recorded submission; False for a duplicate source and URL.

        Raises:
            ValueError: The source is unrelated or the parent is terminal.
            KeyError: Canonical status is missing.
        """
        parent = int(real_job_id)
        source = int(source_job_id)
        now = self._application_time()
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            member = await _fetch_row(
                db, "SELECT 1 FROM jobs WHERE id=? AND real_job_id=?", (source, parent)
            )
            if member is None:
                raise ValueError("source job does not belong to real job")
            status = await _fetch_row(
                db, "SELECT status FROM real_job_status WHERE real_job_id=?", (parent,)
            )
            if status is None:
                raise KeyError(f"no canonical status for real_job_id={real_job_id}")
            existing = await _fetch_row(
                db,
                "SELECT id FROM real_job_applications WHERE real_job_id=? "
                "AND source_job_id=? AND apply_url IS ? LIMIT 1",
                (parent, source, apply_url),
            )
            if existing is not None:
                return False
            old = str(status["status"])
            if is_terminal(old):
                raise ValueError(f"cannot apply from terminal status '{old}'")
            if resume_variant is not None:
                await _register_variant(db, resume_variant, None, now)
            for snapshot in snapshots or []:
                await _save_snapshot(db, snapshot)
            await db.execute(
                "INSERT INTO real_job_applications("
                "real_job_id,source_job_id,apply_url,applied_at,notes,"
                "cover_letter,"
                "application_method,verdict_snapshot,fit_snapshot,hooks_snapshot) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    parent,
                    source,
                    apply_url,
                    _require_utc_timestamp(record.applied_at),
                    record.notes,
                    record.cover_letter,
                    record.application_method,
                    record.verdict_snapshot,
                    record.fit_snapshot,
                    record.hooks_snapshot,
                ),
            )
            if old in ACTIVE_APPLICATION_STATUSES:
                await db.execute(
                    "INSERT INTO real_job_status_history("
                    "real_job_id,from_status,to_status,changed_at,reason,"
                    "resume_variant_at_change) VALUES(?,?,?,?,?,?)",
                    (
                        parent,
                        old,
                        old,
                        _require_utc_timestamp(now),
                        "record_application_noop",
                        resume_variant,
                    ),
                )
                if resume_variant is not None:
                    await db.execute(
                        "UPDATE real_job_status SET resume_variant=? "
                        "WHERE real_job_id=?",
                        (resume_variant, parent),
                    )
            else:
                await self._transition_real_in_tx(
                    db,
                    TransitionRequest(
                        job_id=real_job_id,
                        new_status="applied",
                        reason="record_application",
                        resume_variant=resume_variant,
                    ),
                )
        return True

    async def list_real_job_applications(
        self, *, limit: int = 100
    ) -> list[RealJobApplicationEvent]:
        """List submission events without exposing resume snapshot fields.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            Canonical submission events ordered newest first.

        Raises:
            ValueError: The requested limit is negative.
        """
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        async with self._lifecycle.connection() as db:
            rows = await _fetch_rows(
                db,
                "SELECT id,real_job_id,source_job_id,source_applied_job_id,"
                "apply_url,applied_at,application_method,notes "
                "FROM real_job_applications ORDER BY applied_at DESC,id DESC LIMIT ?",
                (limit,),
            )
        return [
            RealJobApplicationEvent(
                id=int(row["id"]),
                real_job_id=str(row["real_job_id"]),
                source_job_id=str(row["source_job_id"])
                if row["source_job_id"] is not None
                else None,
                source_applied_job_id=str(row["source_applied_job_id"])
                if row["source_applied_job_id"] is not None
                else None,
                apply_url=row["apply_url"],
                applied_at=_parse_utc_timestamp(row["applied_at"]),
                application_method=row["application_method"],
                notes=row["notes"],
            )
            for row in rows
        ]

    async def auto_decay_real_jobs(
        self, *, ghost_days: int = 30, archive_ignored_days: int = 14
    ) -> AutoDecayResult:
        """Decay each canonical decision once, independent of source aliases.

        Args:
            ghost_days: Age after which an unanswered application becomes ghosted.
            archive_ignored_days: Age after which an ignored parent becomes archived.

        Returns:
            Counts of parents advanced to ghosted and archived.
        """
        now = self._application_time()
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            ghosts = await _fetch_rows(
                db,
                "SELECT real_job_id FROM real_job_status WHERE status IN "
                "('applied','interviewing') AND last_status_change_at<?",
                (_require_utc_timestamp(now - timedelta(days=ghost_days)),),
            )
            archives = await _fetch_rows(
                db,
                "SELECT real_job_id FROM real_job_status WHERE status='ignored' "
                "AND last_status_change_at<?",
                (_require_utc_timestamp(now - timedelta(days=archive_ignored_days)),),
            )
            for row in ghosts:
                await self._transition_real_in_tx(
                    db,
                    TransitionRequest(
                        job_id=str(row["real_job_id"]),
                        new_status="ghosted",
                        reason="auto_decay",
                        force=True,
                    ),
                )
            for row in archives:
                await self._transition_real_in_tx(
                    db,
                    TransitionRequest(
                        job_id=str(row["real_job_id"]),
                        new_status="archived",
                        reason="auto_decay",
                        force=True,
                    ),
                )
        return AutoDecayResult(ghosted=len(ghosts), archived=len(archives))

    async def real_job_workflow_attention(
        self, *, auto_ghost_days: int = 30, lookahead_days: int = 5
    ) -> WorkflowAttention:
        """Build workflow reminder buckets from canonical rows only.

        Args:
            auto_ghost_days: Ghosting age used to project the upcoming reminder window.
            lookahead_days: Days ahead included in interview and ghosting reminders.

        Returns:
            Canonical follow-up, interview, and ghosting reminder buckets.
        """
        now = self._application_time()
        columns = (
            "s.real_job_id AS job_id,j.title,j.company,j.url,s.status,"
            "s.last_status_change_at,s.next_followup_at,s.notes"
        )
        joins = (
            "FROM real_job_status s JOIN real_jobs r ON r.id=s.real_job_id "
            "JOIN jobs j ON j.id=r.representative_job_id "
        )
        async with self._lifecycle.connection() as db:
            follow = await _fetch_rows(
                db,
                f"SELECT {columns} {joins} WHERE s.status IN "
                "('applied','interviewing') AND s.next_followup_at IS NOT NULL "
                "AND date(s.next_followup_at)<=date(?) "
                "ORDER BY s.next_followup_at",
                (_require_utc_timestamp(now),),
            )
            interview = await _fetch_rows(
                db,
                f"SELECT {columns} {joins} WHERE s.status='interviewing' "
                "AND (EXISTS(SELECT 1 FROM real_job_interview_rounds ir "
                "WHERE ir.real_job_id=s.real_job_id AND ir.completed_at IS NULL "
                "AND ir.scheduled_at IS NOT NULL AND ir.scheduled_at<=?) "
                "OR NOT EXISTS(SELECT 1 FROM real_job_interview_rounds ir "
                "WHERE ir.real_job_id=s.real_job_id AND ir.completed_at IS NULL "
                "AND ir.scheduled_at IS NOT NULL)) "
                "ORDER BY s.last_status_change_at DESC",
                (_require_utc_timestamp(now + timedelta(days=lookahead_days)),),
            )
            ghosting = await _fetch_rows(
                db,
                f"SELECT {columns} {joins} WHERE s.status IN "
                "('applied','interviewing') AND s.last_status_change_at<? "
                "ORDER BY s.last_status_change_at",
                (
                    _require_utc_timestamp(
                        now - timedelta(days=auto_ghost_days - lookahead_days)
                    ),
                ),
            )
        return WorkflowAttention(
            follow_up_today=[
                _attention_item(row, "follow-up due", now) for row in follow
            ],
            interview_prep=[
                _attention_item(row, "interview prep", now) for row in interview
            ],
            going_ghosted=[
                _attention_item(row, "going silent", now) for row in ghosting
            ],
        )

    async def real_job_application_stats(
        self, *, since_days_ago: int | None = 30, by_resume: bool = False
    ) -> ApplicationStats:
        """Count submitted real jobs and responses after their first event.

        Args:
            since_days_ago: Optional submission cohort age; None includes all events.
            by_resume: Whether to group outcomes by recorded resume variant.

        Returns:
            Submission-cohort counts, outcomes, response latency, and resume breakdown.
        """
        cutoff = (
            None
            if since_days_ago is None
            else _require_utc_timestamp(
                self._application_time() - timedelta(days=since_days_ago)
            )
        )
        async with self._lifecycle.connection() as db:
            applied = await _fetch_rows(
                db,
                "SELECT a.id,a.real_job_id AS job_id,a.applied_at AS changed_at,"
                "(SELECT h.resume_variant_at_change FROM real_job_status_history h "
                "WHERE h.real_job_id=a.real_job_id AND h.reason IN "
                "('record_application','record_application_noop') "
                "AND h.changed_at>=a.applied_at "
                "ORDER BY h.changed_at,h.id LIMIT 1) AS resume_variant_at_change "
                "FROM real_job_applications a "
                "WHERE (? IS NULL OR a.applied_at>=?) "
                "ORDER BY a.applied_at,a.id",
                (cutoff, cutoff),
            )
            first = _first_apply_by_job(applied)
            if not first:
                return _empty_stats()
            ids = sorted(first)
            histories = await _fetch_rows(
                db,
                "SELECT id,real_job_id AS job_id,to_status,changed_at,reason "
                "FROM real_job_status_history WHERE real_job_id IN "
                f"({','.join('?' for _ in ids)}) ORDER BY changed_at,id",
                ids,
            )
        outcomes: dict[int, set[str]] = {}
        first_response: dict[int, datetime] = {}
        for row in histories:
            job_id = int(row["job_id"])
            changed_at = _parse_utc_timestamp(row["changed_at"])
            if (
                changed_at <= _parse_utc_timestamp(first[job_id]["changed_at"])
                or row["to_status"] not in RESPONSE_STATUSES
                or row["reason"] == "record_application_noop"
            ):
                continue
            outcomes.setdefault(job_id, set()).add(str(row["to_status"]))
            first_response.setdefault(job_id, changed_at)
        deltas = [
            max(0, (at - _parse_utc_timestamp(first[job_id]["changed_at"])).days)
            for job_id, at in first_response.items()
        ]
        responses, interviews, offers, rejections = _count_outcomes(outcomes)
        return ApplicationStats(
            applied_count=len(first),
            response_count=responses,
            interview_count=interviews,
            offer_count=offers,
            rejection_count=rejections,
            median_days_to_response=_median(deltas),
            by_resume=_by_resume_stats(first, outcomes) if by_resume else None,
        )

    async def compute_real_job_reapply_notice(
        self, *, job_id: str, lookback_days: int = 60
    ) -> str | None:
        """Exclude all aliases of the submitted canonical job from the notice.

        Args:
            job_id: Source posting ID used only to resolve its canonical parent.
            lookback_days: How far back to search for active same-company applications.

        Returns:
            A same-company warning, or None when no other active application exists.

        Raises:
            ValueError: The source exists but has no canonical parent.
        """
        now = self._application_time()
        async with self._lifecycle.connection() as db:
            source = await _fetch_row(
                db,
                "SELECT real_job_id,company,company_norm FROM jobs WHERE id=?",
                (int(job_id),),
            )
            if source is None:
                return None
            if source["real_job_id"] is None:
                raise ValueError(f"source job {job_id} has no real-job parent")
            company = source["company_norm"] or normalize_company(source["company"])
            if not company:
                return None
            rows = await _fetch_row(
                db,
                "SELECT r.id,j.title,s.status FROM real_job_status s "
                "JOIN real_jobs r ON r.id=s.real_job_id "
                "JOIN jobs j ON j.id=r.representative_job_id "
                "WHERE j.company_norm=? AND r.id!=? "
                "AND s.status IN ('applied','interviewing') "
                "AND s.last_status_change_at>=? LIMIT 1",
                (
                    company,
                    source["real_job_id"],
                    _require_utc_timestamp(now - timedelta(days=lookback_days)),
                ),
            )
        if rows is None:
            return None
        return (
            "Active application at same company: "
            f"'{rows['title']}' (real job {rows['id']}, status={rows['status']})"
        )

    async def resolve_real_job_id(self, source_job_id: str) -> str | None:
        """Resolve a source posting ID to its canonical parent without ID guessing.

        Args:
            source_job_id: Source posting ID that supplied this event or lookup.

        Returns:
            Canonical parent ID, or None for an unknown or unmigrated source.

        Raises:
            ValueError: The source exists but its canonical parent is unresolved.
        """
        async with self._lifecycle.connection() as db:
            try:
                row = await _fetch_row(
                    db, "SELECT real_job_id FROM jobs WHERE id=?", (int(source_job_id),)
                )
            except aiosqlite.OperationalError as exc:
                if "no such column: real_job_id" not in str(exc):
                    raise
                return None
        if row is None:
            return None
        if row[0] is None:
            raise ValueError(f"source job {source_job_id} has no real-job parent")
        return str(row[0])

    async def get_real_job_status(self, real_job_id: str) -> StatusInfo | None:
        """Read one canonical status with representative company and title.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical status, or None when the parent has no status row.
        """
        async with self._lifecycle.connection() as db:
            row = await _fetch_row(
                db,
                "SELECT s.*,j.company,j.title FROM real_job_status s "
                "JOIN real_jobs r ON r.id=s.real_job_id "
                "LEFT JOIN jobs j ON j.id=r.representative_job_id "
                "WHERE s.real_job_id=?",
                (int(real_job_id),),
            )
        if row is None:
            return None
        return StatusInfo(
            job_id=str(row["real_job_id"]),
            status=row["status"],
            next_followup_at=_parse_utc_timestamp(row["next_followup_at"])
            if row["next_followup_at"]
            else None,
            resume_variant=row["resume_variant"],
            notes=row["notes"],
            last_status_change_at=_parse_utc_timestamp(row["last_status_change_at"]),
            company=row["company"],
            title=row["title"],
        )

    async def list_real_job_statuses(
        self, filters: StatusFilter | None = None
    ) -> list[StatusInfo]:
        """List one canonical workflow row per real job with CLI filters.

        Args:
            filters: Optional status or Results eligibility filters.

        Returns:
            Matching canonical statuses ordered by most recent change.

        Raises:
            ValueError: The requested limit is negative.
        """
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
            params.append(_require_utc_timestamp(query.since))
        if query.no_response_days is not None:
            clauses.append("s.status IN ('applied','interviewing')")
            clauses.append("s.last_status_change_at<?")
            params.append(
                _require_utc_timestamp(now - timedelta(days=query.no_response_days))
            )
        if query.needs_followup:
            clauses.append("s.next_followup_at IS NOT NULL")
            clauses.append("date(s.next_followup_at)<=date(?)")
            params.append(_require_utc_timestamp(now))
        if query.notes_contain:
            clauses.append("unicode_casefold(s.notes) LIKE unicode_casefold(?)")
            params.append(f"%{query.notes_contain}%")
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = (
            "SELECT s.real_job_id,s.status,s.next_followup_at,s.resume_variant,"
            "s.notes,s.last_status_change_at,j.company,j.title "
            "FROM real_job_status s JOIN real_jobs r ON r.id=s.real_job_id "
            f"JOIN jobs j ON j.id=r.representative_job_id WHERE {where} "
            "ORDER BY s.last_status_change_at DESC"
        )
        if query.limit is not None:
            if query.limit < 0:
                raise ValueError("limit must be nonnegative")
            sql += " LIMIT ?"
            params.append(query.limit)
        async with self._lifecycle.connection() as db:
            rows = await _fetch_rows(db, sql, params)
        return [
            StatusInfo(
                job_id=str(row["real_job_id"]),
                status=row["status"],
                next_followup_at=_parse_utc_timestamp(row["next_followup_at"])
                if row["next_followup_at"]
                else None,
                resume_variant=row["resume_variant"],
                notes=row["notes"],
                last_status_change_at=_parse_utc_timestamp(
                    row["last_status_change_at"]
                ),
                company=row["company"],
                title=row["title"],
            )
            for row in rows
        ]

    async def get_real_job_status_history(self, real_job_id: str) -> list[str]:
        """Read canonical destination statuses, most recent first.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Destination status names ordered newest first.
        """
        async with self._lifecycle.connection() as db:
            rows = await _fetch_rows(
                db,
                "SELECT to_status FROM real_job_status_history "
                "WHERE real_job_id=? ORDER BY id DESC",
                (int(real_job_id),),
            )
        return [str(row["to_status"]) for row in rows]

    async def transition_real_job_status(self, request: TransitionRequest) -> str:
        """Validate and persist one canonical decision with history.

        Args:
            request: Canonical transition request and its force/reason fields.

        Returns:
            Persisted destination status name.
        """
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            return await self._transition_real_in_tx(db, request)

    async def _transition_real_in_tx(
        self, db: aiosqlite.Connection, request: TransitionRequest
    ) -> str:
        row = await _fetch_row(
            db,
            "SELECT status FROM real_job_status WHERE real_job_id=?",
            (int(request.job_id),),
        )
        if row is None:
            raise KeyError(f"no canonical status for real_job_id={request.job_id}")
        old = str(row["status"])
        error = validate_transition(
            old, request.new_status, force=request.force, i_mean_it=request.i_mean_it
        )
        if error is not None:
            raise ValueError(error)
        now = self._application_time()
        followup = (
            now + timedelta(days=request.followup_grace_days)
            if request.new_status == "applied"
            else None
        )
        reset = (
            request.new_status not in ACTIVE_APPLICATION_STATUSES
            or followup is not None
        )
        await db.execute(
            "UPDATE real_job_status SET status=?,"
            "next_followup_at=CASE WHEN ? THEN ? ELSE next_followup_at END,"
            "resume_variant=COALESCE(?,resume_variant),last_status_change_at=? "
            "WHERE real_job_id=?",
            (
                request.new_status,
                reset,
                _require_utc_timestamp(followup) if followup else None,
                request.resume_variant,
                _require_utc_timestamp(now),
                int(request.job_id),
            ),
        )
        await db.execute(
            "INSERT INTO real_job_status_history("
            "real_job_id,from_status,to_status,changed_at,reason,"
            "resume_variant_at_change) "
            "VALUES(?,?,?,?,?,?)",
            (
                int(request.job_id),
                old,
                request.new_status,
                _require_utc_timestamp(now),
                request.reason or ("FORCE" if request.force else None),
                request.resume_variant,
            ),
        )
        return request.new_status

    async def transition_real_jobs_bulk(
        self, request: BulkTransitionRequest
    ) -> BulkResult:
        """Transition each distinct canonical ID independently.

        Args:
            request: Canonical transition request and its force/reason fields.

        Returns:
            Per-item success and failure counts for the submitted IDs.
        """
        result = BulkResult()
        seen: set[str] = set()
        for real_id, status in request.items:
            if real_id in seen:
                continue
            seen.add(real_id)
            try:
                await self.transition_real_job_status(
                    TransitionRequest(
                        job_id=real_id,
                        new_status=status,
                        reason=request.reason_selected,
                        force=request.force,
                        i_mean_it=request.i_mean_it,
                    )
                )
            except Exception as exc:
                result.failed.append((real_id, str(exc)))
            else:
                result.succeeded += 1
        return result

    async def append_real_job_note(self, *, real_job_id: str, text: str) -> bool:
        """Append a timestamped note to one canonical status.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            text: Note text to append.

        Returns:
            True when the parent status was updated.
        """
        now = self._application_time()
        line = now.strftime("[%Y-%m-%d %H:%M] ") + text + "\n"
        async with self._lifecycle.connection() as db:
            cursor = await db.execute(
                "UPDATE real_job_status SET notes=COALESCE(notes,'')||?,"
                "last_status_change_at=? WHERE real_job_id=?",
                (line, _require_utc_timestamp(now), int(real_job_id)),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed == 1

    async def set_real_job_followup(self, *, real_job_id: str, at: datetime) -> bool:
        """Set the next follow-up time on one canonical status.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            at: Timezone-aware follow-up timestamp.

        Returns:
            True when the parent status was updated.
        """
        timestamp = _require_utc_timestamp(self._application_time(at), "at")
        async with self._lifecycle.connection() as db:
            cursor = await db.execute(
                "UPDATE real_job_status SET next_followup_at=? WHERE real_job_id=?",
                (timestamp, int(real_job_id)),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed == 1

    async def restore_real_job(self, real_job_id: str) -> str:
        """Restore an archived or ghosted parent to its previous active state.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Restored destination status name.

        Raises:
            KeyError: The parent has no canonical status.
            ValueError: The current state cannot be restored.
        """
        info = await self.get_real_job_status(real_job_id)
        if info is None:
            raise KeyError(f"no canonical status for real_job_id={real_job_id}")
        if info.status not in {"ghosted", "archived"}:
            raise ValueError(
                f"restore requires ghosted or archived, got {info.status!r}"
            )
        target = pick_restore_target(
            await self.get_real_job_status_history(real_job_id)
        )
        target = target or "applied"
        return await self.transition_real_job_status(
            TransitionRequest(
                job_id=real_job_id,
                new_status=target,
                reason="restore",
                force=True,
                i_mean_it=True,
            )
        )

    async def add_real_job_interview(
        self, *, real_job_id: str, label: str, scheduled_at: datetime | None = None
    ) -> InterviewRound:
        """Create the next interview round and advance an applied parent.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            label: Interview round label.
            scheduled_at: Optional scheduled interview time.

        Returns:
            The created canonical interview round.

        Raises:
            KeyError: The parent has no canonical status.
        """
        now = self._application_time()
        scheduled = (
            _require_utc_timestamp(self._application_time(scheduled_at))
            if scheduled_at
            else None
        )
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            row = await _fetch_row(
                db,
                "SELECT status FROM real_job_status WHERE real_job_id=?",
                (int(real_job_id),),
            )
            if row is None:
                raise KeyError(f"no canonical status for real_job_id={real_job_id}")
            cursor = await db.execute(
                "INSERT INTO real_job_interview_rounds("
                "real_job_id,round_index,label,scheduled_at,created_at) "
                "SELECT ?,COALESCE(MAX(round_index),0)+1,?,?,? "
                "FROM real_job_interview_rounds WHERE real_job_id=? RETURNING *",
                (
                    int(real_job_id),
                    label,
                    scheduled,
                    _require_utc_timestamp(now),
                    int(real_job_id),
                ),
            )
            added = await cursor.fetchone()
            await cursor.close()
            if row["status"] == "applied":
                await self._transition_real_in_tx(
                    db, TransitionRequest(job_id=real_job_id, new_status="interviewing")
                )
            await db.execute(
                "UPDATE real_job_status SET last_status_change_at=? "
                "WHERE real_job_id=?",
                (_require_utc_timestamp(now), int(real_job_id)),
            )
        assert added is not None
        return _round(added)

    async def list_real_job_interviews(self, real_job_id: str) -> list[InterviewRound]:
        """List interview rounds for one canonical parent in round order.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical interview rounds in ascending round order.
        """
        async with self._lifecycle.connection() as db:
            rows = await _fetch_rows(
                db,
                "SELECT * FROM real_job_interview_rounds "
                "WHERE real_job_id=? ORDER BY round_index",
                (int(real_job_id),),
            )
        return [_round(row) for row in rows]

    async def complete_real_job_interview(
        self,
        *,
        real_job_id: str,
        round_index: int | None = None,
        notes: str | None = None,
    ) -> InterviewRound:
        """Complete the requested open round or the latest open round.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            round_index: Optional exact round; otherwise choose the latest open round.
            notes: Optional completion notes.

        Returns:
            The completed canonical interview round.

        Raises:
            ValueError: No matching open interview round exists.
        """
        now = self._application_time()
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            rows = await _fetch_rows(
                db,
                "SELECT * FROM real_job_interview_rounds "
                "WHERE real_job_id=? AND completed_at IS NULL "
                "ORDER BY round_index DESC",
                (int(real_job_id),),
            )
            target = next(
                (
                    row
                    for row in rows
                    if round_index is None or row["round_index"] == round_index
                ),
                None,
            )
            if target is None:
                raise ValueError(
                    f"no open interview round for real_job_id={real_job_id}"
                )
            await db.execute(
                "UPDATE real_job_interview_rounds SET completed_at=?,notes=? "
                "WHERE id=?",
                (_require_utc_timestamp(now), notes, target["id"]),
            )
            await db.execute(
                "UPDATE real_job_status SET last_status_change_at=? "
                "WHERE real_job_id=?",
                (_require_utc_timestamp(now), int(real_job_id)),
            )
            row = await _fetch_row(
                db,
                "SELECT * FROM real_job_interview_rounds WHERE id=?",
                (target["id"],),
            )
        assert row is not None
        return _round(row)


def _round(row: aiosqlite.Row) -> InterviewRound:
    return InterviewRound(
        id=int(row["id"]),
        job_id=int(row["real_job_id"]),
        round_index=int(row["round_index"]),
        label=str(row["label"]),
        scheduled_at=_parse_utc_timestamp(row["scheduled_at"])
        if row["scheduled_at"]
        else None,
        completed_at=_parse_utc_timestamp(row["completed_at"])
        if row["completed_at"]
        else None,
        notes=row["notes"],
        created_at=_parse_utc_timestamp(row["created_at"]),
    )
