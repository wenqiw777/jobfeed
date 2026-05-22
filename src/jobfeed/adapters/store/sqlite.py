"""SQLite implementation of the JobStore port."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

import aiosqlite

from jobfeed.adapters.store import _sqlite_application as _app
from jobfeed.adapters.store import _sqlite_ops as _ops
from jobfeed.adapters.store import _sqlite_status as _st
from jobfeed.adapters.store.sqlite_mapping import (
    evaluation_from_row,
    job_from_row,
    pipeline_run_from_row,
    row_to_dict,
)
from jobfeed.adapters.store.sqlite_params import (
    job_id_value,
    job_params,
    pipeline_run_params,
    stage_a_params,
    stage_b_params,
)
from jobfeed.adapters.store.sqlite_sql import (
    INSERT_JOB_DO_NOTHING_SQL,
    INSERT_PIPELINE_RUN_SQL,
    LIST_EVALUATED_SQL,
    PENDING_STAGE_A_SQL,
    PENDING_STAGE_B_SQL,
    SAVE_STAGE_A_ERROR_SQL,
    SAVE_STAGE_A_SQL,
    SAVE_STAGE_B_ERROR_SQL,
    SAVE_STAGE_B_SQL,
    UPDATE_JOB_SQL,
)
from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    AttentionReport,
    AutoDecayResult,
    CompanyRecord,
    CostEntry,
    DigestStats,
    JobEvaluation,
    JobPosting,
    MLGateResult,
    PipelineRun,
    ResumeSnapshot,
    SaveJobResult,
    StageAResult,
    StageBResult,
    StatusInfo,
    WorkflowAttention,
)


class SQLiteStore:
    """SQLite-backed JobStore implementation."""

    def __init__(self, db_path: Path) -> None:
        """Create a store for a SQLite database path.

        Args:
            db_path: SQLite file path.
        """
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._connection_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open SQLite connection and initialize schema.

        Raises:
            Exception: If initialization fails.
        """
        async with self._connection_lock:
            if self._db is not None:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(self.db_path)
            try:
                db.row_factory = sqlite3.Row
                await db.execute("PRAGMA foreign_keys = ON")
                schema = Path(__file__).with_name("schema.sql").read_text("utf-8")
                await db.executescript(schema)
            except Exception:
                await db.close()
                raise
            self._db = db

    async def close(self) -> None:
        """Close the SQLite connection when open."""
        async with self._connection_lock:
            if self._db is not None:
                db = self._db
                self._db = None
                await db.close()

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Insert or update a job by source identity.

        Args:
            job: Job posting to persist.

        Returns:
            Upsert result.
        Raises:
            RuntimeError: Unresolvable conflict.
        """
        async with self._connection_lock:
            db = self._connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                result = await self._save_job_in_transaction(job)
            except Exception:
                await db.rollback()
                raise
            await db.commit()
            return result

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load a job by store identity.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Job posting if found, else None.
        """
        row = await self._fetchone(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id_value(job_id),),
        )
        return job_from_row(row_to_dict(row)) if row is not None else None

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List recent jobs.

        Args:
            limit: Max jobs.

        Returns:
            Job postings by recency.
        """
        rows = await self._fetchall(
            "SELECT * FROM jobs ORDER BY discovered_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [job_from_row(row_to_dict(row)) for row in rows]

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None:
        """Persist a successful Stage A result.

        Args:
            job_id: Store-assigned job identity.
            result: Stage A result.
        """
        await self._execute(SAVE_STAGE_A_SQL, stage_a_params(job_id, result))

    async def save_stage_a_error(self, job_id: str, error: str) -> None:
        """Record a Stage A error (retryable).

        Args:
            job_id: Store-assigned job identity.
            error: Error detail.
        """
        await self._execute(SAVE_STAGE_A_ERROR_SQL, (job_id_value(job_id), error))

    async def save_stage_b(self, job_id: str, result: StageBResult) -> None:
        """Persist a successful Stage B result.

        Args:
            job_id: Store-assigned job identity.
            result: Stage B result.
        """
        await self._execute(SAVE_STAGE_B_SQL, stage_b_params(job_id, result))

    async def save_stage_b_error(self, job_id: str, error: str) -> None:
        """Record a Stage B error (retryable).

        Args:
            job_id: Store-assigned job identity.
            error: Error detail.
        """
        await self._execute(SAVE_STAGE_B_ERROR_SQL, (job_id_value(job_id), error))

    async def load_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load jobs pending Stage A (filters applied in Task 3).

        Args:
            limit: Max jobs.
            quality_bands: jd_quality filter.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter.

        Returns:
            Jobs pending Stage A.
        """
        rows = await self._fetchall(PENDING_STAGE_A_SQL, (limit,))
        return [job_from_row(row_to_dict(row)) for row in rows]

    async def load_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load Stage A-completed, Stage B-pending jobs.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.

        Returns:
            Jobs pending Stage B.
        """
        rows = await self._fetchall(PENDING_STAGE_B_SQL, (limit,))
        return [job_from_row(row_to_dict(row)) for row in rows]

    async def list_evaluated_jobs(self, limit: int = 100) -> list[JobEvaluation]:
        """List jobs with evaluations.

        Args:
            limit: Max evaluations.

        Returns:
            Joined evaluations.
        """
        rows = await self._fetchall(LIST_EVALUATED_SQL, (limit,))
        return [evaluation_from_row(row_to_dict(row)) for row in rows]

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Persist pipeline run counters.

        Args:
            run: Pipeline run.
        """
        await self._execute(INSERT_PIPELINE_RUN_SQL, pipeline_run_params(run))

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load a pipeline run by identity.

        Args:
            run_id: Run identity.

        Returns:
            Pipeline run if found, else None.
        """
        row = await self._fetchone(
            "SELECT * FROM pipeline_runs WHERE run_id = ?",
            (run_id,),
        )
        return pipeline_run_from_row(row_to_dict(row)) if row is not None else None

    # --- delegated to _sqlite_status ---

    async def transition_status(
        self,
        *,
        job_id: str,
        new_status: str,
        reason: str | None = None,
        resume_variant: str | None = None,
        force: bool = False,
        i_mean_it: bool = False,
        followup_grace_days: int = 7,
    ) -> str:
        """Delegate to status mixin.

        Args:
            job_id: Job identity.
            new_status: Target status.
            reason: Optional reason.
            resume_variant: Optional variant.
            force: Bypass graph.
            i_mean_it: Double-gate.
            followup_grace_days: Follow-up days.

        Returns:
            New status.
        """
        return await _st.transition_status(
            self._connection(),
            self._connection_lock,
            job_id=job_id,
            new_status=new_status,
            reason=reason,
            resume_variant=resume_variant,
            force=force,
            i_mean_it=i_mean_it,
            followup_grace_days=followup_grace_days,
        )

    async def get_status(self, job_id: str) -> StatusInfo | None:
        """Delegate to status mixin.

        Args:
            job_id: Job identity.

        Returns:
            Status info if found.
        """
        return await _st.get_status(self._connection(), self._connection_lock, job_id)

    async def restore_from_archived(self, job_id: str) -> str:
        """Delegate to status mixin.

        Args:
            job_id: Job identity.

        Returns:
            Restored status.
        """
        return await _st.restore_from_archived(
            self._connection(), self._connection_lock, job_id
        )

    async def auto_decay(
        self, *, ghost_days: int = 30, archive_ignored_days: int = 14
    ) -> AutoDecayResult:
        """Delegate to status mixin.

        Args:
            ghost_days: Ghost threshold.
            archive_ignored_days: Archive threshold.

        Returns:
            Decay counts.
        """
        return await _st.auto_decay(
            self._connection(),
            self._connection_lock,
            ghost_days=ghost_days,
            archive_ignored_days=archive_ignored_days,
        )

    async def list_statuses(
        self,
        *,
        statuses: frozenset[str] | None = None,
        days: int | None = None,
        no_response_days: int | None = None,
        needs_followup: bool = False,
        notes_contain: str | None = None,
        limit: int | None = None,
    ) -> list[StatusInfo]:
        """Delegate to status mixin.

        Args:
            statuses: Status filter.
            days: Days filter.
            no_response_days: Response filter.
            needs_followup: Follow-up filter.
            notes_contain: Notes filter.
            limit: Max results.

        Returns:
            Status info records.
        """
        return await _st.list_statuses(
            self._connection(),
            self._connection_lock,
            statuses=statuses,
            days=days,
            no_response_days=no_response_days,
            needs_followup=needs_followup,
            notes_contain=notes_contain,
            limit=limit,
        )

    async def append_note(self, *, job_id: str, text: str) -> None:
        """Delegate to status mixin.

        Args:
            job_id: Job identity.
            text: Note text.
        """
        await _st.append_note(
            self._connection(), self._connection_lock, job_id=job_id, text=text
        )

    async def workflow_attention(
        self, *, auto_ghost_days: int = 30, lookahead_days: int = 5
    ) -> WorkflowAttention:
        """Delegate to status mixin.

        Args:
            auto_ghost_days: Ghost threshold.
            lookahead_days: Lookahead.

        Returns:
            Attention report.
        """
        return await _st.workflow_attention(
            self._connection(),
            self._connection_lock,
            auto_ghost_days=auto_ghost_days,
            lookahead_days=lookahead_days,
        )

    async def compute_reapply_notice(
        self, *, job_id: str, lookback_days: int = 60
    ) -> str | None:
        """Delegate to status mixin.

        Args:
            job_id: Job identity.
            lookback_days: Lookback.

        Returns:
            Notice if detected.
        """
        return await _st.compute_reapply_notice(
            self._connection(),
            self._connection_lock,
            job_id=job_id,
            lookback_days=lookback_days,
        )

    # --- delegated to _sqlite_application ---

    async def record_application(self, record: ApplicationRecord) -> bool:
        """Delegate to application mixin.

        Args:
            record: Application record.

        Returns:
            True if new.

        Raises:
            Exception: On transaction failure.
        """
        return await _app.record_application(
            self._connection(), self._connection_lock, record
        )

    async def list_applications(self, *, limit: int = 100) -> list[ApplicationRecord]:
        """Delegate to application mixin.

        Args:
            limit: Max records.

        Returns:
            Application records.
        """
        return await _app.list_applications(
            self._connection(), self._connection_lock, limit=limit
        )

    async def application_stats(
        self, *, since_days_ago: int = 30, by_resume: bool = False
    ) -> ApplicationStats:
        """Delegate to application mixin.

        Args:
            since_days_ago: Time window.
            by_resume: Variant breakdown.

        Returns:
            Application statistics.
        """
        return await _app.application_stats(
            self._connection(),
            self._connection_lock,
            since_days_ago=since_days_ago,
            by_resume=by_resume,
        )

    async def save_resume_snapshot(self, snapshot: ResumeSnapshot) -> None:
        """Delegate to application mixin.

        Args:
            snapshot: Resume snapshot.
        """
        await _app.save_resume_snapshot(
            self._connection(), self._connection_lock, snapshot
        )

    async def get_resume_snapshot(self, resume_hash: str) -> ResumeSnapshot | None:
        """Delegate to application mixin.

        Args:
            resume_hash: Content hash.

        Returns:
            Snapshot if found.
        """
        return await _app.get_resume_snapshot(
            self._connection(), self._connection_lock, resume_hash
        )

    async def register_resume_variant(
        self, *, name: str, description: str | None = None
    ) -> bool:
        """Delegate to application mixin.

        Args:
            name: Variant name.
            description: Description.

        Returns:
            True if new.
        """
        return await _app.register_resume_variant(
            self._connection(),
            self._connection_lock,
            name=name,
            description=description,
        )

    # --- delegated to _sqlite_ops ---

    async def job_exists(self, *, platform: str, canonical_id: str) -> bool:
        """Delegate to ops mixin.

        Args:
            platform: Platform.
            canonical_id: Identity.

        Returns:
            True if exists.
        """
        return await _ops.job_exists(
            self._connection(),
            self._connection_lock,
            platform=platform,
            canonical_id=canonical_id,
        )

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        """Delegate to ops mixin.

        Args:
            job_id: Job identity.
        """
        await _ops.mark_stage_b_skipped(
            self._connection(), self._connection_lock, job_id
        )

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Delegate to ops mixin.

        Args:
            job_id: Job identity.

        Returns:
            Evaluation if found.
        """
        return await _ops.get_evaluation(
            self._connection(), self._connection_lock, job_id
        )

    async def top_evaluated_jobs(
        self, *, min_score: int = 0, limit: int = 100
    ) -> list[JobEvaluation]:
        """Delegate to ops mixin.

        Args:
            min_score: Score threshold.
            limit: Max results.

        Returns:
            Sorted evaluations.
        """
        return await _ops.top_evaluated_jobs(
            self._connection(), self._connection_lock, min_score=min_score, limit=limit
        )

    async def save_ml_gate_result(self, job_id: str, result: MLGateResult) -> None:
        """Delegate to ops mixin.

        Args:
            job_id: Job identity.
            result: Gate decision.
        """
        await _ops.save_ml_gate_result(
            self._connection(), self._connection_lock, job_id, result
        )

    async def upsert_company(self, company: CompanyRecord) -> None:
        """Delegate to ops mixin.

        Args:
            company: Company record.
        """
        await _ops.upsert_company(self._connection(), self._connection_lock, company)

    async def get_company(self, slug: str) -> CompanyRecord | None:
        """Delegate to ops mixin.

        Args:
            slug: Company slug.

        Returns:
            Company if found.
        """
        return await _ops.get_company(self._connection(), self._connection_lock, slug)

    async def list_companies(
        self, *, vendor: str | None = None, include_removed: bool = False
    ) -> list[CompanyRecord]:
        """Delegate to ops mixin.

        Args:
            vendor: Vendor filter.
            include_removed: Include removed.

        Returns:
            Company records.
        """
        return await _ops.list_companies(
            self._connection(),
            self._connection_lock,
            vendor=vendor,
            include_removed=include_removed,
        )

    async def mark_company_removed(self, slug: str) -> bool:
        """Delegate to ops mixin.

        Args:
            slug: Company slug.

        Returns:
            True if matched.
        """
        return await _ops.mark_company_removed(
            self._connection(), self._connection_lock, slug
        )

    async def bump_discover_failure(self, slug: str) -> int:
        """Delegate to ops mixin.

        Args:
            slug: Company slug.

        Returns:
            New failure count.
        """
        return await _ops.bump_discover_failure(
            self._connection(), self._connection_lock, slug
        )

    async def reset_discover_failures(self, slug: str) -> None:
        """Delegate to ops mixin.

        Args:
            slug: Company slug.
        """
        await _ops.reset_discover_failures(
            self._connection(), self._connection_lock, slug
        )

    async def record_enrichment(
        self,
        *,
        job_id: str,
        jd_text: str,
        jd_quality: str,
        enriched_at: datetime,
        enrich_source: str,
        jd_lang: str | None = None,
    ) -> None:
        """Delegate to ops mixin.

        Args:
            job_id: Job identity.
            jd_text: JD body.
            jd_quality: Quality band.
            enriched_at: Timestamp.
            enrich_source: Source.
            jd_lang: Language.
        """
        await _ops.record_enrichment(
            self._connection(),
            self._connection_lock,
            job_id=job_id,
            jd_text=jd_text,
            jd_quality=jd_quality,
            enriched_at=enriched_at,
            enrich_source=enrich_source,
            jd_lang=jd_lang,
        )

    async def enrich_paste(
        self, *, platform: str, canonical_id: str, jd_text: str
    ) -> str:
        """Delegate to ops mixin.

        Args:
            platform: Platform.
            canonical_id: Identity.
            jd_text: Pasted JD.

        Returns:
            Job identity.

        Raises:
            ValueError: If job not found.
        """
        return await _ops.enrich_paste(
            self._connection(),
            self._connection_lock,
            platform=platform,
            canonical_id=canonical_id,
            jd_text=jd_text,
        )

    async def get_state(self, key: str) -> str | None:
        """Delegate to ops mixin.

        Args:
            key: State key.

        Returns:
            Value if found.
        """
        return await _ops.get_state(self._connection(), self._connection_lock, key)

    async def set_state(self, key: str, value: str) -> None:
        """Delegate to ops mixin.

        Args:
            key: State key.
            value: State value.
        """
        await _ops.set_state(self._connection(), self._connection_lock, key, value)

    async def record_cost(self, *, day: str, spent_usd: float) -> None:
        """Delegate to ops mixin.

        Args:
            day: Date.
            spent_usd: Cost.
        """
        await _ops.record_cost(
            self._connection(), self._connection_lock, day=day, spent_usd=spent_usd
        )

    async def get_cost(self, day: str) -> CostEntry | None:
        """Delegate to ops mixin.

        Args:
            day: Date.

        Returns:
            Cost entry if found.
        """
        return await _ops.get_cost(self._connection(), self._connection_lock, day)

    async def get_cost_range(self, *, since_days: int = 30) -> list[CostEntry]:
        """Delegate to ops mixin.

        Args:
            since_days: Days back.

        Returns:
            Cost entries.
        """
        return await _ops.get_cost_range(
            self._connection(), self._connection_lock, since_days=since_days
        )

    async def digest_stats(self, *, threshold: int = 60) -> DigestStats:
        """Delegate to ops mixin.

        Args:
            threshold: Score threshold.

        Returns:
            Digest stats.
        """
        return await _ops.digest_stats(
            self._connection(), self._connection_lock, threshold=threshold
        )

    async def needs_attention(
        self, *, days: int = 7, max_per_category: int = 10
    ) -> AttentionReport:
        """Delegate to ops mixin.

        Args:
            days: Lookback.
            max_per_category: Max items.

        Returns:
            Attention report.
        """
        return await _ops.needs_attention(
            self._connection(),
            self._connection_lock,
            days=days,
            max_per_category=max_per_category,
        )

    # --- private helpers ---

    async def _save_job_in_transaction(self, job: JobPosting) -> SaveJobResult:
        db = self._connection()
        cursor = await db.execute(INSERT_JOB_DO_NOTHING_SQL, job_params(job))
        if cursor.rowcount == 1:
            return SaveJobResult(
                job_id=str(cast(int, cursor.lastrowid)),
                inserted=True,
                updated=False,
            )
        row = await self._connection().execute(
            "SELECT id FROM jobs WHERE platform = ? AND canonical_id = ?",
            (job.platform, job.canonical_id),
        )
        existing = await row.fetchone()
        if existing is None:
            raise RuntimeError("job upsert conflict did not resolve to a row")
        job_id = cast(int, row_to_dict(existing)["id"])
        await db.execute(UPDATE_JOB_SQL, (*job_params(job), job_id))
        return SaveJobResult(job_id=str(job_id), inserted=False, updated=True)

    async def _execute(self, sql: str, params: tuple[object, ...]) -> None:
        async with self._connection_lock:
            db = self._connection()
            await db.execute(sql, params)
            await db.commit()

    async def _fetchone(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> sqlite3.Row | None:
        async with self._connection_lock:
            cursor = await self._connection().execute(sql, params)
            return await cursor.fetchone()

    async def _fetchall(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> list[sqlite3.Row]:
        async with self._connection_lock:
            cursor = await self._connection().execute(sql, params)
            return cast(list[sqlite3.Row], await cursor.fetchall())

    def _connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteStore is not connected")
        return self._db
