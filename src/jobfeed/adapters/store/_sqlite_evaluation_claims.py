"""SQLite evaluation claim capability with short guarded write transactions."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_rows,
    _hydrate_job,
    _immediate_transaction,
    _placeholders,
    _require_utc_timestamp,
)
from jobfeed.adapters.store._sqlite_claim_filters import (
    StageAQuery,
    StageBQuery,
    _build_stage_a_select,
    _build_stage_b_select,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import JobPosting
from jobfeed.ports.store_claims import GateCandidate


class _SqliteEvaluationClaims:
    """Implement paid-work claim operations over an injected lifecycle."""

    _lifecycle: SqliteLifecycle

    async def claim_pending_stage_a(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Claim ordered Stage A work using caller-supplied UTC time."""
        query = StageAQuery(
            now=self._claim_time(now),
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
        )
        return await self._claim_stage_a(query)

    async def preview_claimable_stage_a(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Preview the exact ordered Stage A eligibility set without mutation."""
        query = StageAQuery(
            now=self._claim_time(now),
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
        )
        sql, params = _build_stage_a_select(query)
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(connection, sql, params)
        return [_hydrate_job(row) for row in rows]

    async def load_gate_candidates(  # noqa: PLR0913
        self,
        *,
        now: datetime | None = None,
        corpus: str = "unrated",
        quality_bands: frozenset[str] | None = None,
        max_days: int | None = None,
        limit: int = 100,
        exclude_gate_failed: bool = True,
        after: tuple[datetime, int] | None = None,
        job_ids: list[str] | None = None,
    ) -> list[GateCandidate]:
        """Load a keyset page of read-only ML-gate candidates."""
        query = StageAQuery(
            now=self._claim_time(now),
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            exclude_gate_failed=exclude_gate_failed,
            after=after,
            is_gate_query=True,
            job_ids=None if job_ids is None else _numeric_ids(job_ids),
        )
        sql, params = _build_stage_a_select(query)
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(connection, sql, params)
        return [
            GateCandidate(job=_hydrate_job(row), ml_gate_result=row["ml_gate_result"])
            for row in rows
        ]

    async def claim_stage_a_by_ids(  # noqa: PLR0913
        self,
        job_ids: list[str],
        *,
        now: datetime | None = None,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
        limit: int = 100,
    ) -> list[JobPosting]:
        """Claim the eligible numeric subset of explicit job identities."""
        numeric_ids = _numeric_ids(job_ids)
        if not numeric_ids:
            return []
        query = StageAQuery(
            now=self._claim_time(now),
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            job_ids=numeric_ids,
        )
        return await self._claim_stage_a(query)

    async def claim_pending_stage_b(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
        job_ids: list[str] | None = None,
    ) -> list[JobPosting]:
        """Claim ordered Stage B work using strict one-hour stale recovery."""
        claim_time = self._claim_time(now)
        query = StageBQuery(
            now=claim_time,
            limit=limit,
            max_days=max_days,
            stage_a_threshold=stage_a_threshold,
            job_ids=None if job_ids is None else _numeric_ids(job_ids),
        )
        sql, params = _build_stage_b_select(query)
        timestamp = _require_utc_timestamp(claim_time)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            rows = await _fetch_rows(connection, sql, params)
            claimed_job_ids = tuple(int(row["id"]) for row in rows)
            if claimed_job_ids:
                await connection.execute(
                    "UPDATE evaluations SET stage_b_status='in_progress', "
                    f"updated_at=? WHERE job_id IN ({_placeholders(claimed_job_ids)})",
                    (timestamp, *claimed_job_ids),
                )
            await self._after_claim_selection("stage_b", connection)
        return [_hydrate_job(row) for row in rows]

    async def release_stage_a_claim(
        self, job_id: str, *, now: datetime | None = None
    ) -> None:
        """Idempotently restore the prior observable Stage A claim state."""
        await self._release_claim(job_id, now=self._claim_time(now), stage="a")

    async def release_stage_b_claim(
        self, job_id: str, *, now: datetime | None = None
    ) -> None:
        """Idempotently restore the prior observable Stage B claim state."""
        await self._release_claim(job_id, now=self._claim_time(now), stage="b")

    async def refresh_stage_b_claim(
        self, job_id: str, *, now: datetime | None = None
    ) -> None:
        """Advance caller-owned time only for an active Stage B claim."""
        numeric_id = int(job_id)
        timestamp = _require_utc_timestamp(self._claim_time(now))
        async with self._lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE evaluations SET updated_at=? "
                "WHERE job_id=? AND stage_b_status='in_progress'",
                (timestamp, numeric_id),
            )

    async def _claim_stage_a(self, query: StageAQuery) -> list[JobPosting]:
        sql, params = _build_stage_a_select(query)
        timestamp = _require_utc_timestamp(query.now)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            rows = await _fetch_rows(connection, sql, params)
            for row in rows:
                await connection.execute(
                    """INSERT INTO evaluations (
                           job_id, stage_a_status, created_at, updated_at
                       ) VALUES (?, 'in_progress', ?, ?)
                       ON CONFLICT(job_id) DO UPDATE SET
                           stage_a_status='in_progress',
                           updated_at=excluded.updated_at""",
                    (int(row["id"]), timestamp, timestamp),
                )
            await self._after_claim_selection("stage_a", connection)
        return [_hydrate_job(row) for row in rows]

    async def _release_claim(
        self,
        job_id: str,
        *,
        now: datetime,
        stage: str,
    ) -> None:
        numeric_id = int(job_id)
        timestamp = _require_utc_timestamp(now)
        value_column = "stage_a_score" if stage == "a" else "stage_b_verdict"
        await self._execute_release(
            numeric_id,
            timestamp=timestamp,
            stage=stage,
            value_column=value_column,
        )

    async def _execute_release(
        self,
        job_id: int,
        *,
        timestamp: str,
        stage: str,
        value_column: str,
    ) -> None:
        async with self._lifecycle.connection() as connection:
            await connection.execute(
                f"""UPDATE evaluations SET
                       stage_{stage}_status=CASE
                           WHEN stage_{stage}_error IS NOT NULL THEN 'error'
                           WHEN {value_column} IS NOT NULL THEN 'completed'
                           ELSE NULL END,
                       updated_at=?
                   WHERE job_id=? AND stage_{stage}_status='in_progress'""",
                (timestamp, job_id),
            )

    async def _after_claim_selection(
        self,
        stage: str,
        connection: aiosqlite.Connection,
    ) -> None:
        del stage, connection

    def _claim_time(self, value: datetime | None) -> datetime:
        if value is None:
            raise ValueError("claim time requires an injected application clock")
        return value


def _numeric_ids(job_ids: list[str]) -> tuple[int, ...]:
    numeric: set[int] = set()
    for job_id in job_ids:
        try:
            numeric.add(int(job_id))
        except ValueError:
            continue
    return tuple(sorted(numeric))
