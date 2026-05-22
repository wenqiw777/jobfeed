"""SQLite implementation of the JobStore port."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import cast

import aiosqlite

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
    JobEvaluation,
    JobPosting,
    PipelineRun,
    SaveJobResult,
    StageAResult,
    StageBResult,
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
