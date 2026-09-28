"""PostgreSQL canonical workflow commands, separate from source-row audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median

import asyncpg  # type: ignore[import-untyped]

from jobfeed.adapters.store._normalize import normalize_company
from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models_application import (
    ApplicationRecord,
    ApplicationStats,
    RealJobApplicationEvent,
    ResumeSnapshot,
    ResumeVariantStats,
)
from jobfeed.domain.models_status import (
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    StatusFilter,
    StatusInfo,
    TransitionRequest,
    WorkflowAttention,
    WorkflowAttentionItem,
)
from jobfeed.domain.status import (
    ACTIVE_APPLICATION_STATUSES,
    RESPONSE_STATUSES,
    is_terminal,
    pick_restore_target,
    validate_transition,
)


class PostgresRealJobWorkflow:
    """PostgreSQL canonical workflow commands and source-audit preserving reads."""

    def _get_pool(self) -> asyncpg.Pool:
        raise NotImplementedError

    async def _save_resume_snapshot_in_tx(
        self, conn: asyncpg.Connection, snapshot: ResumeSnapshot
    ) -> None:
        """Require the concrete store to persist an application snapshot."""
        raise NotImplementedError

    async def auto_decay_real_jobs(
        self, *, ghost_days: int = 30, archive_ignored_days: int = 14
    ) -> AutoDecayResult:
        """Decay each canonical decision once and retain source audit states.

        Args:
            ghost_days: Age after which an unanswered application becomes ghosted.
            archive_ignored_days: Age after which an ignored parent becomes archived.

        Returns:
            Counts of parents advanced to ghosted and archived.
        """
        now = datetime.now(UTC)
        async with self._get_pool().acquire() as db, db.transaction():
            ghosts = await db.fetch(
                "SELECT real_job_id FROM real_job_status WHERE status = ANY($1) "
                "AND last_status_change_at<$2 FOR UPDATE",
                sorted(ACTIVE_APPLICATION_STATUSES),
                now - timedelta(days=ghost_days),
            )
            archives = await db.fetch(
                "SELECT real_job_id FROM real_job_status WHERE status='ignored' "
                "AND last_status_change_at<$1 FOR UPDATE",
                now - timedelta(days=archive_ignored_days),
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
        """Read workflow reminders once per canonical job.

        Args:
            auto_ghost_days: Ghosting age used to project the upcoming reminder window.
            lookahead_days: Days ahead included in interview and ghosting reminders.

        Returns:
            Canonical follow-up, interview, and ghosting reminder buckets.
        """
        now = datetime.now(UTC)
        columns = (
            "s.real_job_id AS job_id,j.title,j.company,j.url,s.status,"
            "s.last_status_change_at,s.next_followup_at,s.notes"
        )
        joins = (
            "FROM real_job_status s JOIN real_jobs r ON r.id=s.real_job_id "
            "JOIN jobs j ON j.id=r.representative_job_id "
        )
        async with self._get_pool().acquire() as db:
            follow = await db.fetch(
                f"SELECT {columns} {joins} WHERE s.status = ANY($1) "
                "AND s.next_followup_at IS NOT NULL "
                "AND s.next_followup_at::date<=CURRENT_DATE "
                "ORDER BY s.next_followup_at",
                sorted(ACTIVE_APPLICATION_STATUSES),
            )
            interview = await db.fetch(
                f"SELECT {columns} {joins} WHERE s.status='interviewing' "
                "AND (EXISTS(SELECT 1 FROM real_job_interview_rounds ir "
                "WHERE ir.real_job_id=s.real_job_id AND ir.completed_at IS NULL "
                "AND ir.scheduled_at IS NOT NULL AND ir.scheduled_at<=$1) "
                "OR NOT EXISTS(SELECT 1 FROM real_job_interview_rounds ir "
                "WHERE ir.real_job_id=s.real_job_id AND ir.completed_at IS NULL "
                "AND ir.scheduled_at IS NOT NULL)) "
                "ORDER BY s.last_status_change_at DESC",
                now + timedelta(days=lookahead_days),
            )
            ghosting = await db.fetch(
                f"SELECT {columns} {joins} WHERE s.status = ANY($1) "
                "AND s.last_status_change_at<$2 ORDER BY s.last_status_change_at",
                sorted(ACTIVE_APPLICATION_STATUSES),
                now - timedelta(days=auto_ghost_days - lookahead_days),
            )
        return WorkflowAttention(
            follow_up_today=[
                _real_attention(row, "follow-up due", now) for row in follow
            ],
            interview_prep=[
                _real_attention(row, "interview prep", now) for row in interview
            ],
            going_ghosted=[
                _real_attention(row, "going silent", now) for row in ghosting
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
            else datetime.now(UTC) - timedelta(days=since_days_ago)
        )
        async with self._get_pool().acquire() as db:
            applied = await db.fetch(
                "SELECT a.id,a.real_job_id,a.applied_at AS changed_at,"
                "(SELECT h.resume_variant_at_change FROM real_job_status_history h "
                "WHERE h.real_job_id=a.real_job_id AND h.reason IN "
                "('record_application','record_application_noop') "
                "AND h.changed_at>=a.applied_at "
                "ORDER BY h.changed_at,h.id LIMIT 1) AS resume_variant_at_change "
                "FROM real_job_applications a "
                "WHERE ($1::timestamptz IS NULL OR a.applied_at>=$1) "
                "ORDER BY a.applied_at,a.id",
                cutoff,
            )
            first: dict[int, asyncpg.Record] = {}
            for row in applied:
                first.setdefault(row["real_job_id"], row)
            if not first:
                return ApplicationStats(
                    applied_count=0,
                    response_count=0,
                    interview_count=0,
                    offer_count=0,
                    rejection_count=0,
                )
            histories = await db.fetch(
                "SELECT id,real_job_id,to_status,changed_at,reason "
                "FROM real_job_status_history WHERE real_job_id = ANY($1) "
                "ORDER BY changed_at,id",
                sorted(first),
            )
        outcomes: dict[int, set[str]] = {}
        first_response: dict[int, datetime] = {}
        for row in histories:
            real_id = row["real_job_id"]
            if row["changed_at"] <= first[real_id]["changed_at"]:
                continue
            if (
                row["to_status"] not in RESPONSE_STATUSES
                or row["reason"] == "record_application_noop"
            ):
                continue
            outcomes.setdefault(real_id, set()).add(row["to_status"])
            first_response.setdefault(real_id, row["changed_at"])
        deltas = [
            max(0, (at - first[real_id]["changed_at"]).days)
            for real_id, at in first_response.items()
        ]
        variants: dict[str, list[int]] = {}
        for real_id, row in first.items():
            variants.setdefault(
                row["resume_variant_at_change"] or "unknown", []
            ).append(real_id)
        resume_stats = None
        if by_resume:
            resume_stats = {}
            for name, ids in sorted(variants.items()):
                matched = [outcomes[i] for i in ids if i in outcomes]
                resume_stats[name] = ResumeVariantStats(
                    sent=len(ids),
                    responses=len(matched),
                    interviews=sum(
                        bool(s & {"interviewing", "offer"}) for s in matched
                    ),
                    offers=sum("offer" in s for s in matched),
                    rejections=sum("rejected" in s for s in matched),
                )
        return ApplicationStats(
            applied_count=len(first),
            response_count=len(outcomes),
            interview_count=sum(
                bool(s & {"interviewing", "offer"}) for s in outcomes.values()
            ),
            offer_count=sum("offer" in s for s in outcomes.values()),
            rejection_count=sum("rejected" in s for s in outcomes.values()),
            median_days_to_response=float(median(deltas)) if deltas else None,
            by_resume=resume_stats,
        )

    async def compute_real_job_reapply_notice(
        self, *, job_id: str, lookback_days: int = 60
    ) -> str | None:
        """Exclude aliases of the current parent from same-company notices.

        Args:
            job_id: Source posting ID used only to resolve its canonical parent.
            lookback_days: How far back to search for active same-company applications.

        Returns:
            A same-company warning, or None when no other active application exists.

        Raises:
            ValueError: The source exists but has no canonical parent.
        """
        async with self._get_pool().acquire() as db:
            source = await db.fetchrow(
                "SELECT real_job_id,company,company_norm FROM jobs WHERE id=$1",
                int(job_id),
            )
            if source is None:
                return None
            if source["real_job_id"] is None:
                raise ValueError(f"source job {job_id} has no real-job parent")
            company = source["company_norm"] or normalize_company(source["company"])
            if not company:
                return None
            match = await db.fetchrow(
                "SELECT r.id,j.title,s.status FROM real_job_status s "
                "JOIN real_jobs r ON r.id=s.real_job_id "
                "JOIN jobs j ON j.id=r.representative_job_id "
                "WHERE j.company_norm=$1 AND r.id!=$2 "
                "AND s.status = ANY($3) AND s.last_status_change_at>=$4 "
                "LIMIT 1",
                company,
                source["real_job_id"],
                sorted(ACTIVE_APPLICATION_STATUSES),
                datetime.now(UTC) - timedelta(days=lookback_days),
            )
        if match is None:
            return None
        return (
            "Active application at same company: "
            f"'{match['title']}' (real job {match['id']}, status={match['status']})"
        )

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
        """Record one submission and canonical decision in one transaction.

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
        async with self._get_pool().acquire() as db, db.transaction():
            if (
                await db.fetchval(
                    "SELECT id FROM real_jobs WHERE id=$1 FOR UPDATE", parent
                )
                is None
            ):
                raise KeyError(f"no real job for real_job_id={real_job_id}")
            status = await db.fetchrow(
                "SELECT status FROM real_job_status WHERE real_job_id=$1 FOR UPDATE",
                parent,
            )
            if status is None:
                raise KeyError(f"no canonical status for real_job_id={real_job_id}")
            member = await db.fetchval(
                "SELECT 1 FROM jobs WHERE id=$1 AND real_job_id=$2", source, parent
            )
            if member is None:
                raise ValueError("source job does not belong to real job")
            existing = await db.fetchval(
                "SELECT id FROM real_job_applications WHERE real_job_id=$1 "
                "AND source_job_id=$2 AND apply_url IS NOT DISTINCT FROM $3 "
                "LIMIT 1",
                parent,
                source,
                apply_url,
            )
            if existing is not None:
                return False
            old = status["status"]
            if is_terminal(old):
                raise ValueError(f"cannot apply from terminal status '{old}'")
            if resume_variant is not None:
                await db.execute(
                    "INSERT INTO resume_variants(name) VALUES($1) "
                    "ON CONFLICT(name) DO NOTHING",
                    resume_variant,
                )
            for snapshot in snapshots or []:
                await self._save_resume_snapshot_in_tx(db, snapshot)
            await db.execute(
                "INSERT INTO real_job_applications("
                "real_job_id,source_job_id,apply_url,applied_at,notes,"
                "cover_letter,"
                "application_method,verdict_snapshot,fit_snapshot,hooks_snapshot) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                parent,
                source,
                apply_url,
                record.applied_at,
                record.notes,
                record.cover_letter,
                record.application_method,
                record.verdict_snapshot,
                record.fit_snapshot,
                record.hooks_snapshot,
            )
            if old in ACTIVE_APPLICATION_STATUSES:
                await db.execute(
                    "INSERT INTO real_job_status_history("
                    "real_job_id,from_status,to_status,changed_at,reason,"
                    "resume_variant_at_change) VALUES($1,$2,$3,$4,$5,$6)",
                    parent,
                    old,
                    old,
                    datetime.now(UTC),
                    "record_application_noop",
                    resume_variant,
                )
                if resume_variant is not None:
                    await db.execute(
                        "UPDATE real_job_status SET resume_variant=$1 "
                        "WHERE real_job_id=$2",
                        resume_variant,
                        parent,
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
        """List canonical submission provenance without resume contents.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            Canonical submission events ordered newest first.

        Raises:
            ValueError: The requested limit is negative.
        """
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        async with self._get_pool().acquire() as db:
            rows = await db.fetch(
                "SELECT id,real_job_id,source_job_id,source_applied_job_id,"
                "apply_url,applied_at,application_method,notes "
                "FROM real_job_applications ORDER BY applied_at DESC,id DESC LIMIT $1",
                limit,
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
                applied_at=row["applied_at"],
                application_method=row["application_method"],
                notes=row["notes"],
            )
            for row in rows
        ]

    async def backfill_real_job_workflow(self) -> int:
        """Copy unambiguous legacy decisions after source ownership reconciliation.

        Returns:
            Number of parent statuses copied from legacy source rows.
        """
        copied = 0
        async with self._get_pool().acquire() as db, db.transaction():
            parents = await db.fetch(
                "SELECT r.id FROM real_jobs r LEFT JOIN real_job_status s "
                "ON s.real_job_id=r.id WHERE s.real_job_id IS NULL "
                "OR s.status IN ('new','scored') ORDER BY r.id"
            )
            for parent in parents:
                real_id = parent["id"]
                rows = await db.fetch(
                    "SELECT j.id,s.status,s.next_followup_at,s.resume_variant,s.notes,"
                    "s.last_status_change_at FROM jobs j JOIN job_status s "
                    "ON s.job_id=j.id WHERE j.real_job_id=$1 "
                    "ORDER BY s.last_status_change_at DESC,j.id DESC",
                    real_id,
                )
                explicit = {
                    row["status"]
                    for row in rows
                    if row["status"] not in {"new", "scored"}
                }
                if len(explicit) > 1:
                    await db.execute(
                        "UPDATE real_jobs SET identity_review_state='status_conflict' "
                        "WHERE id=$1",
                        real_id,
                    )
                    continue
                if not rows:
                    continue
                chosen = next(
                    (row for row in rows if row["status"] in explicit), rows[0]
                )
                await db.execute(
                    "INSERT INTO real_job_status(real_job_id,status,next_followup_at,"
                    "resume_variant,notes,last_status_change_at) "
                    "VALUES($1,$2,$3,$4,$5,$6) "
                    "ON CONFLICT DO NOTHING",
                    real_id,
                    chosen["status"],
                    chosen["next_followup_at"],
                    chosen["resume_variant"],
                    chosen["notes"],
                    chosen["last_status_change_at"],
                )
                if explicit:
                    await db.execute(
                        "UPDATE real_job_status SET status=$1,next_followup_at=$2,"
                        "resume_variant=$3,notes=$4,last_status_change_at=$5 "
                        "WHERE real_job_id=$6 AND status IN ('new','scored')",
                        chosen["status"],
                        chosen["next_followup_at"],
                        chosen["resume_variant"],
                        chosen["notes"],
                        chosen["last_status_change_at"],
                        real_id,
                    )
                await db.execute(
                    "INSERT INTO real_job_status_history(real_job_id,source_history_id,"
                    "from_status,to_status,changed_at,reason,resume_variant_at_change) "
                    "SELECT $1,id,from_status,to_status,changed_at,reason,"
                    "resume_variant_at_change FROM job_status_history WHERE job_id=$2 "
                    "ON CONFLICT(source_history_id) DO NOTHING",
                    real_id,
                    chosen["id"],
                )
                await db.execute(
                    "INSERT INTO real_job_interview_rounds(real_job_id,source_round_id,"
                    "round_index,label,scheduled_at,completed_at,notes,created_at) "
                    "SELECT $1,id,round_index,label,scheduled_at,completed_at,"
                    "notes,created_at FROM interview_rounds WHERE job_id=$2 "
                    "ON CONFLICT(source_round_id) DO NOTHING",
                    real_id,
                    chosen["id"],
                )
                copied += 1
            await db.execute(
                "INSERT INTO real_job_applications("
                "real_job_id,source_job_id,source_applied_job_id,apply_url,applied_at,"
                "notes,cover_letter,"
                "application_method,verdict_snapshot,fit_snapshot,hooks_snapshot) "
                "SELECT j.real_job_id,a.job_id,a.job_id,NULL,a.applied_at,a.notes,"
                "a.cover_letter,"
                "a.application_method,a.verdict_snapshot,a.fit_snapshot,"
                "a.hooks_snapshot "
                "FROM applied a JOIN jobs j ON j.id=a.job_id "
                "WHERE j.real_job_id IS NOT NULL "
                "ON CONFLICT(source_applied_job_id) DO NOTHING"
            )
        return copied

    async def resolve_real_job_id(self, source_job_id: str) -> str | None:
        """Resolve a source posting ID to its canonical parent without ID guessing.

        Args:
            source_job_id: Source posting ID that supplied this event or lookup.

        Returns:
            Canonical parent ID, or None for an unknown or unmigrated source.

        Raises:
            ValueError: The source exists but its canonical parent is unresolved.
        """
        async with self._get_pool().acquire() as db:
            try:
                row = await db.fetchrow(
                    "SELECT real_job_id FROM jobs WHERE id=$1", int(source_job_id)
                )
            except asyncpg.UndefinedColumnError:
                return None
        if row is None:
            return None
        if row["real_job_id"] is None:
            raise ValueError(f"source job {source_job_id} has no real-job parent")
        return str(row["real_job_id"])

    async def get_real_job_status(self, real_job_id: str) -> StatusInfo | None:
        """Read one canonical status with representative company and title.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical status, or None when the parent has no status row.
        """
        async with self._get_pool().acquire() as db:
            row = await db.fetchrow(
                "SELECT s.*,j.company,j.title FROM real_job_status s "
                "JOIN real_jobs r ON r.id=s.real_job_id "
                "LEFT JOIN jobs j ON j.id=r.representative_job_id "
                "WHERE s.real_job_id=$1",
                int(real_job_id),
            )
        if row is None:
            return None
        return StatusInfo(
            job_id=str(row["real_job_id"]),
            status=row["status"],
            next_followup_at=row["next_followup_at"],
            resume_variant=row["resume_variant"],
            notes=row["notes"],
            last_status_change_at=row["last_status_change_at"],
            company=row["company"],
            title=row["title"],
        )

    async def list_real_job_statuses(
        self, filters: StatusFilter | None = None
    ) -> list[StatusInfo]:
        """List canonical status rows using the CLI's status filters.

        Args:
            filters: Optional status or Results eligibility filters.

        Returns:
            Matching canonical statuses ordered by most recent change.

        Raises:
            ValueError: The requested limit is negative.
        """
        query = filters or StatusFilter()
        clauses: list[str] = []
        params: list[object] = []

        def bind(value: object) -> str:
            params.append(value)
            return f"${len(params)}"

        if query.statuses:
            clauses.append(f"s.status=ANY({bind(sorted(query.statuses))})")
        if query.days is not None:
            threshold = datetime.now(UTC) - timedelta(days=query.days)
            clauses.append(f"s.last_status_change_at>={bind(threshold)}")
        if query.since is not None:
            clauses.append(f"s.last_status_change_at>={bind(query.since)}")
        if query.no_response_days is not None:
            clauses.append("s.status IN ('applied','interviewing')")
            clauses.append(
                "s.last_status_change_at<"
                + bind(datetime.now(UTC) - timedelta(days=query.no_response_days))
            )
        if query.needs_followup:
            clauses.append("s.next_followup_at IS NOT NULL")
            clauses.append("s.next_followup_at::date<=CURRENT_DATE")
        if query.notes_contain:
            clauses.append(f"s.notes ILIKE '%'||{bind(query.notes_contain)}||'%'")
        where = " AND ".join(clauses) if clauses else "TRUE"
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
            sql += f" LIMIT {bind(query.limit)}"
        async with self._get_pool().acquire() as db:
            rows = await db.fetch(sql, *params)
        return [
            StatusInfo(
                job_id=str(row["real_job_id"]),
                status=row["status"],
                next_followup_at=row["next_followup_at"],
                resume_variant=row["resume_variant"],
                notes=row["notes"],
                last_status_change_at=row["last_status_change_at"],
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
        async with self._get_pool().acquire() as db:
            rows = await db.fetch(
                "SELECT to_status FROM real_job_status_history "
                "WHERE real_job_id=$1 ORDER BY id DESC",
                int(real_job_id),
            )
        return [row["to_status"] for row in rows]

    async def _transition_real_in_tx(
        self, db: asyncpg.Connection, request: TransitionRequest
    ) -> str:
        if (
            await db.fetchval(
                "SELECT id FROM real_jobs WHERE id=$1 FOR UPDATE", int(request.job_id)
            )
            is None
        ):
            raise KeyError(f"no real job for real_job_id={request.job_id}")
        row = await db.fetchrow(
            "SELECT status FROM real_job_status WHERE real_job_id=$1 FOR UPDATE",
            int(request.job_id),
        )
        if row is None:
            raise KeyError(f"no canonical status for real_job_id={request.job_id}")
        old = row["status"]
        error = validate_transition(
            old, request.new_status, force=request.force, i_mean_it=request.i_mean_it
        )
        if error is not None:
            raise ValueError(error)
        now = datetime.now(UTC)
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
            "UPDATE real_job_status SET status=$1,"
            "next_followup_at=CASE WHEN $2 THEN $3 ELSE next_followup_at END,"
            "resume_variant=COALESCE($4,resume_variant),last_status_change_at=$5 "
            "WHERE real_job_id=$6",
            request.new_status,
            reset,
            followup,
            request.resume_variant,
            now,
            int(request.job_id),
        )
        await db.execute(
            "INSERT INTO real_job_status_history("
            "real_job_id,from_status,to_status,changed_at,reason,"
            "resume_variant_at_change) "
            "VALUES($1,$2,$3,$4,$5,$6)",
            int(request.job_id),
            old,
            request.new_status,
            now,
            request.reason or ("FORCE" if request.force else None),
            request.resume_variant,
        )
        return request.new_status

    async def transition_real_job_status(self, request: TransitionRequest) -> str:
        """Validate and persist one canonical decision with history.

        Args:
            request: Canonical transition request and its force/reason fields.

        Returns:
            Persisted destination status name.
        """
        async with self._get_pool().acquire() as db, db.transaction():
            return await self._transition_real_in_tx(db, request)

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
        now = datetime.now(UTC)
        line = now.strftime("[%Y-%m-%d %H:%M] ") + text + "\n"
        async with self._get_pool().acquire() as db:
            result = await db.execute(
                "UPDATE real_job_status SET notes=COALESCE(notes,'')||$1,"
                "last_status_change_at=$2 WHERE real_job_id=$3",
                line,
                now,
                int(real_job_id),
            )
        return bool(result == "UPDATE 1")

    async def set_real_job_followup(self, *, real_job_id: str, at: datetime) -> bool:
        """Set the next follow-up time on one canonical status.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            at: Timezone-aware follow-up timestamp.

        Returns:
            True when the parent status was updated.

        Raises:
            ValueError: The follow-up timestamp has no timezone.
        """
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("follow-up must be timezone aware")
        async with self._get_pool().acquire() as db:
            result = await db.execute(
                "UPDATE real_job_status SET next_followup_at=$1 WHERE real_job_id=$2",
                at,
                int(real_job_id),
            )
        return bool(result == "UPDATE 1")

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
        now = datetime.now(UTC)
        async with self._get_pool().acquire() as db, db.transaction():
            state = await db.fetchrow(
                "SELECT status FROM real_job_status WHERE real_job_id=$1 FOR UPDATE",
                int(real_job_id),
            )
            if state is None:
                raise KeyError(f"no canonical status for real_job_id={real_job_id}")
            index = await db.fetchval(
                "SELECT COALESCE(MAX(round_index),0)+1 "
                "FROM real_job_interview_rounds WHERE real_job_id=$1",
                int(real_job_id),
            )
            row = await db.fetchrow(
                "INSERT INTO real_job_interview_rounds("
                "real_job_id,round_index,label,scheduled_at,created_at) "
                "VALUES($1,$2,$3,$4,$5) RETURNING *",
                int(real_job_id),
                index,
                label,
                scheduled_at,
                now,
            )
            if state["status"] == "applied":
                await self._transition_real_in_tx(
                    db, TransitionRequest(job_id=real_job_id, new_status="interviewing")
                )
            await db.execute(
                "UPDATE real_job_status SET last_status_change_at=$1 "
                "WHERE real_job_id=$2",
                now,
                int(real_job_id),
            )
        return _round(row)

    async def list_real_job_interviews(self, real_job_id: str) -> list[InterviewRound]:
        """List interview rounds for one canonical parent in round order.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical interview rounds in ascending round order.
        """
        async with self._get_pool().acquire() as db:
            rows = await db.fetch(
                "SELECT * FROM real_job_interview_rounds "
                "WHERE real_job_id=$1 ORDER BY round_index",
                int(real_job_id),
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
        now = datetime.now(UTC)
        async with self._get_pool().acquire() as db, db.transaction():
            target = await db.fetchrow(
                "SELECT id FROM real_job_interview_rounds "
                "WHERE real_job_id=$1 AND completed_at IS NULL AND "
                "($2::int IS NULL OR round_index=$2) "
                "ORDER BY round_index DESC LIMIT 1 FOR UPDATE",
                int(real_job_id),
                round_index,
            )
            if target is None:
                raise ValueError(
                    f"no open interview round for real_job_id={real_job_id}"
                )
            row = await db.fetchrow(
                "UPDATE real_job_interview_rounds SET completed_at=$1,notes=$2 "
                "WHERE id=$3 RETURNING *",
                now,
                notes,
                target["id"],
            )
            await db.execute(
                "UPDATE real_job_status SET last_status_change_at=$1 "
                "WHERE real_job_id=$2",
                now,
                int(real_job_id),
            )
        return _round(row)


def _round(row: asyncpg.Record) -> InterviewRound:
    return InterviewRound(
        id=int(row["id"]),
        job_id=int(row["real_job_id"]),
        round_index=int(row["round_index"]),
        label=row["label"],
        scheduled_at=row["scheduled_at"],
        completed_at=row["completed_at"],
        notes=row["notes"],
        created_at=row["created_at"],
    )


def _real_attention(
    row: asyncpg.Record, reason: str, now: datetime
) -> WorkflowAttentionItem:
    followup = row["next_followup_at"]
    baseline = (
        followup
        if reason == "follow-up due" and followup
        else row["last_status_change_at"]
    )
    return WorkflowAttentionItem(
        job_id=str(row["job_id"]),
        title=row["title"],
        company=row["company"],
        url=row["url"],
        status=row["status"],
        last_status_change_at=row["last_status_change_at"],
        next_followup_at=followup,
        notes=row["notes"],
        reason=reason,
        days_since=int((now - baseline).total_seconds() / 86_400),
    )
