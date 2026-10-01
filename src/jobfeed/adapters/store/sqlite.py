"""Compose the Task 2 SQLite core capabilities behind one store facade."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from jobfeed.adapters.store._sqlite_application_identity import (
    record_application_identity as _record_application_identity,
)
from jobfeed.adapters.store._sqlite_intermediary import SqliteIntermediary
from jobfeed.adapters.store._sqlite_real_job_evaluation import (
    SqliteRealJobEvaluation,
    sync_sqlite_real_job_input,
)
from jobfeed.adapters.store._sqlite_real_job_identity import resolve_sqlite_real_job
from jobfeed.adapters.store._sqlite_real_job_views import SqliteRealJobViews
from jobfeed.adapters.store._sqlite_real_job_workflow import SqliteRealJobWorkflow
from jobfeed.adapters.store._sqlite_runs import _get_pipeline_run
from jobfeed.adapters.store._sqlite_values import _job_from_row, _utc_text
from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_jobs_evaluations import SqliteJobsEvaluations
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_ops import SqliteOps
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.adapters.store.sqlite_status_applications import (
    SqliteStatusApplications,
)
from jobfeed.adapters.store.sqlite_views_performance import (
    SqliteViewsPerformance,
)
from jobfeed.domain.errors import CanonicalEvaluationNotReadyError
from jobfeed.domain.models import JobPosting, PipelineRun
from jobfeed.domain.real_job_evaluation import (
    EVALUATION_ACTIVATION_KEY,
    official_closed_at,
    policy_visibility,
    representative_source_id,
)
from jobfeed.domain.scoring import MAX_STAGE_RETRIES
from jobfeed.services.canonical_priority import (
    CanonicalPriorityInput,
    priority_input_for_sources,
)

Clock = Callable[[], datetime]
MAX_REAL_JOB_BACKFILL_PAGE = 1000


async def _refresh_backfilled_real_job_display(
    connection: aiosqlite.Connection, source_ids: list[int]
) -> None:
    """Project representative and official closure for one bounded source page.

    Time complexity: O(N + R) Python grouping work for N source IDs and R
    source rows returned across batches. A parent spanning batches can recur.
    """
    for start in range(0, len(source_ids), 900):
        page = source_ids[start : start + 900]
        placeholders = ",".join("?" for _ in page)
        cursor = await connection.execute(
            f"SELECT DISTINCT real_job_id FROM jobs WHERE id IN ({placeholders})",
            page,
        )
        parents = [int(row[0]) for row in await cursor.fetchall()]
        await cursor.close()
        if not parents:
            continue
        placeholders = ",".join("?" for _ in parents)
        cursor = await connection.execute(
            f"SELECT * FROM jobs WHERE real_job_id IN ({placeholders}) "
            "ORDER BY real_job_id,id",
            parents,
        )
        grouped: dict[int, list[JobPosting]] = defaultdict(list)
        for row in await cursor.fetchall():
            grouped[int(row["real_job_id"])].append(_job_from_row(row))
        await cursor.close()
        await connection.executemany(
            "UPDATE real_jobs SET representative_job_id=?,official_closed_at=? "
            "WHERE id=?",
            [
                (
                    representative_source_id(str(parent_id), jobs),
                    (
                        _utc_text(closure)
                        if (closure := official_closed_at(jobs))
                        else None
                    ),
                    parent_id,
                )
                for parent_id, jobs in grouped.items()
            ],
        )


class SQLiteStore(
    SqliteIntermediary,
    SqliteRealJobEvaluation,
    SqliteRealJobWorkflow,
    SqliteRealJobViews,
    SqliteJobsEvaluations,
    SqliteClaimsRuns,
    SqliteStatusApplications,
    SqliteOps,
    SqliteViewsPerformance,
):
    """Own one lifecycle and compose the complete typed SQLite runtime."""

    def __init__(self, path: Path, *, clock: Clock | None = None) -> None:
        """Create a closed store for one database file.

        Args:
            path: SQLite database file path.
            clock: Application UTC clock used by claims and lease recovery.
        """
        self._lifecycle = SqliteLifecycle(path, ensure_sqlite_schema)
        self._application_clock = clock or _utc_now

    async def connect(self) -> None:
        """Open the schema; RunManager owns stale-run recovery policy."""
        if self._lifecycle.is_open:
            return
        await self._lifecycle.open()

    async def close(self) -> None:
        """Close the shared SQLite lifecycle idempotently."""
        await self._lifecycle.close()

    async def record_application_identity(  # noqa: PLR0913
        self,
        *,
        job_id: str,
        expected_apply_url: str,
        ats_url: str | None,
        state_key: str,
        state_value: str,
        run_id: str | None = None,
        owner_id: str | None = None,
        generation: int | None = None,
    ) -> bool:
        """Atomically record a resolution only if its Apply URL is still current.

        Args:
            job_id: Stored source identity.
            expected_apply_url: Application URL observed before resolution.
            ats_url: Verified ATS URL, or None for an unresolved outcome.
            state_key: Resolution receipt key.
            state_value: Serialized outcome and optional verification facts.
            run_id: Optional scan lease identity.
            owner_id: Optional scan lease owner.
            generation: Optional scan lease generation.

        Returns:
            Whether the source still matches the URL and verification facts.
        """
        return await _record_application_identity(
            self._lifecycle,
            job_id=job_id,
            expected_apply_url=expected_apply_url,
            ats_url=ats_url,
            state_key=state_key,
            state_value=state_value,
            run_id=run_id,
            owner_id=owner_id,
            generation=generation,
        )

    async def backfill_real_job_identifiers(
        self, *, after_id: int = 0, limit: int = 100
    ) -> tuple[int, int]:
        """Explicitly reconcile one bounded source page on a migrated DB copy.

        Args:
            after_id: Exclusive source-ID cursor for the next backfill page.
            limit: Maximum number of records to select.

        Returns:
            Last processed source ID and number of source rows processed.

        Raises:
            ValueError: If limit is outside the inclusive range 1 through 1000.
        """
        if limit < 1 or limit > MAX_REAL_JOB_BACKFILL_PAGE:
            raise ValueError("limit must be between 1 and 1000")
        async with self._lifecycle.connection() as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    "SELECT * FROM jobs WHERE id>? AND real_job_id IS NOT NULL "
                    "ORDER BY id LIMIT ?",
                    (after_id, limit),
                )
                rows = list(await cursor.fetchall())
                await cursor.close()
                for row in rows:
                    merged = await resolve_sqlite_real_job(
                        connection,
                        int(row["id"]),
                        _job_from_row(row),
                        include_content=False,
                    )
                    if merged:
                        await sync_sqlite_real_job_input(connection, int(row["id"]))
                await _refresh_backfilled_real_job_display(
                    connection, [int(row["id"]) for row in rows]
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return (int(rows[-1]["id"]) if rows else after_id, len(rows))

    async def resolve_real_job_ids(self, source_ids: list[str]) -> list[str]:
        """Resolve a source scope once and reject orphaned source rows.

        Args:
            source_ids: Source posting IDs whose canonical parents are required.

        Returns:
            Distinct canonical IDs in first-source order.

        Raises:
            ValueError: If an ID is invalid or a requested source lacks a canonical
                parent.
        """
        if not source_ids:
            return []
        ids = [int(value) for value in dict.fromkeys(source_ids)]
        mapping: dict[int, str] = {}
        async with self._lifecycle.connection() as connection:
            for start in range(0, len(ids), 900):
                page = ids[start : start + 900]
                placeholders = ",".join("?" for _ in page)
                cursor = await connection.execute(
                    f"SELECT id,real_job_id FROM jobs WHERE id IN ({placeholders})",
                    page,
                )
                rows = list(await cursor.fetchall())
                await cursor.close()
                mapping.update(
                    {int(row[0]): str(row[1]) for row in rows if row[1] is not None}
                )
        if len(mapping) != len(ids):
            raise ValueError("evaluation scope contains a missing real-job parent")
        return list(dict.fromkeys(mapping[value] for value in ids))

    async def list_real_job_ids_for_evaluation(  # noqa: PLR0913 - bounded policy query
        self,
        *,
        limit: int,
        stage: str = "both",
        threshold: int = 0,
        before_id: int | None = None,
        max_days: int | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[str]:
        """Bound backlog selection before canonical claim filtering.

        Args:
            limit: Maximum number of records to select.
            stage: Evaluation stage: a, b, or both.
            threshold: Minimum Stage A score for Stage B eligibility.
            before_id: Exclusive canonical-ID bound for descending pagination.
            max_days: Conservative source-age prefilter; claims verify exact age.
            stage_a_policy: Configured Stage A policy used to verify stored scores.
            stage_b_policy: Configured Stage B policy used to verify stored scores.

        Returns:
            Bounded canonical IDs eligible for the requested evaluation stages.

        Raises:
            ValueError: If stage is not a, b, or both.
        """
        if limit <= 0:
            return []
        if stage not in {"a", "b", "both"}:
            raise ValueError("unknown canonical evaluation stage")
        # A configuration change does not authorize another paid evaluation.
        del stage_a_policy, stage_b_policy
        now = self._now()
        stale = _utc_text(now - timedelta(hours=1))

        def pending(stage_name: str) -> str:
            return (
                f"(COALESCE(e.{stage_name}_status,'pending') NOT IN "
                "('completed','in_progress','error') OR "
                f"(e.{stage_name}_status='error' AND "
                f"e.{stage_name}_error_count < {MAX_STAGE_RETRIES}) OR "
                f"(e.{stage_name}_status='in_progress' AND e.updated_at < ?))"
            )

        stage_a = pending("stage_a")
        stage_b = (
            "(e.stage_a_status='completed' AND e.stage_a_score>=? AND "
            + pending("stage_b")
            + ")"
        )
        predicate = {"a": stage_a, "b": stage_b, "both": f"({stage_a} OR {stage_b})"}[
            stage
        ]
        args: list[object] = (
            [stale]
            if stage == "a"
            else [threshold, stale]
            if stage == "b"
            else [stale, threshold, stale]
        )
        age_clause = ""
        if max_days is not None:
            cutoff = _utc_text(now - timedelta(days=max_days))
            age_clause = (
                "AND EXISTS(SELECT 1 FROM jobs d WHERE d.real_job_id=r.id AND "
                "(julianday(d.discovered_at)>=julianday(?) OR "
                "julianday(d.posted_at) BETWEEN julianday(?) AND julianday(?))) "
            )
            args.extend((cutoff, cutoff, _utc_text(now)))
        args.extend((before_id, before_id, limit))
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT r.id FROM real_jobs r LEFT JOIN real_job_evaluations e "
                "ON e.real_job_id=r.id WHERE r.identity_review_state='clear' "
                "AND r.official_closed_at IS NULL "
                "AND EXISTS(SELECT 1 FROM jobs j WHERE j.real_job_id=r.id "
                "AND j.jd_quality IN ('full','good') AND COALESCE(j.jd_text,'')!='') "
                "AND EXISTS(SELECT 1 FROM jobs j WHERE j.real_job_id=r.id "
                "AND COALESCE(j.is_repost,0)=0) "
                f"AND {predicate} {age_clause} AND (? IS NULL OR r.id < ?) "
                "ORDER BY r.id DESC LIMIT ?",
                args,
            )
            rows = list(await cursor.fetchall())
            await cursor.close()
        return [str(row[0]) for row in rows]

    async def canonical_evaluation_ready(self) -> bool:
        """Existing v1 databases stay on the source path until explicit migration.

        Returns:
            True when canonical evaluation is activated; False before activation.

        Raises:
            CanonicalEvaluationNotReadyError: If activation exists without the required
                schema or consistent canonical ownership.
        """
        if await self.get_state(EVALUATION_ACTIVATION_KEY) != "enabled":
            return False
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' "
                "AND name='real_job_evaluations'"
            )
            count_row = await cursor.fetchone()
            assert count_row is not None
            value = count_row[0]
            await cursor.close()
            if not value:
                raise CanonicalEvaluationNotReadyError(
                    "canonical evaluation activation lacks schema"
                )
            for label, sql in (
                (
                    "orphan source",
                    "SELECT 1 FROM jobs WHERE real_job_id IS NULL LIMIT 1",
                ),
                (
                    "workflow backfill",
                    "SELECT 1 FROM real_jobs r LEFT JOIN real_job_status s "
                    "ON s.real_job_id=r.id WHERE r.identity_review_state='clear' "
                    "AND s.real_job_id IS NULL LIMIT 1",
                ),
                (
                    "application backfill",
                    "SELECT 1 FROM applied a LEFT JOIN real_job_applications ra "
                    "ON ra.source_applied_job_id=a.job_id WHERE ra.id IS NULL LIMIT 1",
                ),
                (
                    "evaluation backfill",
                    "SELECT 1 FROM evaluations e JOIN jobs j ON j.id=e.job_id "
                    "JOIN real_jobs r ON r.id=j.real_job_id "
                    "LEFT JOIN real_job_evaluations re ON re.real_job_id=j.real_job_id "
                    "WHERE e.stage_a_status='completed' "
                    "AND re.real_job_id IS NULL "
                    "AND r.identity_review_state NOT IN "
                    "('evaluation_conflict','evaluation_input_conflict',"
                    "'evaluation_input_missing','requirements_conflict') LIMIT 1",
                ),
            ):
                cursor = await connection.execute(sql)
                issue = await cursor.fetchone()
                await cursor.close()
                if issue is not None:
                    raise CanonicalEvaluationNotReadyError(
                        f"canonical evaluation activation has {label}"
                    )
        return True

    async def canonical_policy_pending_counts(
        self,
        *,
        stage_a_policy: dict[str, object],
        stage_b_policy: dict[str, object],
    ) -> dict[str, int]:
        """Count completed scores whose model/policy version is unverified.

        Args:
            stage_a_policy: Configured Stage A policy used to verify stored scores.
            stage_b_policy: Configured Stage B policy used to verify stored scores.

        Returns:
            Counts of completed Stage A and Stage B scores lacking current policy proof.
        """
        a = json.dumps(stage_a_policy, sort_keys=True, separators=(",", ":"))
        b = json.dumps(stage_b_policy, sort_keys=True, separators=(",", ":"))
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "WITH p AS (SELECT json(?) AS a,json(?) AS b) "
                "SELECT "
                "0,0,"
                "SUM(CASE WHEN e.stage_a_status='completed' AND "
                "json_extract(e.input_facts_json,'$.stage_a_policy') IS NULL "
                "THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN e.stage_b_status='completed' AND "
                "json_extract(e.input_facts_json,'$.stage_b_policy') IS NULL "
                "THEN 1 ELSE 0 END) "
                "FROM real_job_evaluations e JOIN real_jobs r "
                "ON r.id=e.real_job_id CROSS JOIN p "
                "WHERE r.identity_review_state='clear'",
                (a, b),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return dict(
            zip(
                (
                    "stage_a_pending",
                    "stage_b_pending",
                    "legacy_stage_a",
                    "legacy_stage_b",
                ),
                (int(value or 0) for value in row),
                strict=True,
            )
        )

    async def canonical_policy_cutover_ready(
        self,
        *,
        stage_a_policy: dict[str, object],
        stage_b_policy: dict[str, object],
    ) -> bool:
        """Fail closed when completed scores lack the configured policy proof.

        Args:
            stage_a_policy: Configured Stage A policy used to verify stored scores.
            stage_b_policy: Configured Stage B policy used to verify stored scores.

        Returns:
            True when no completed scores remain unverified for the configured policies.

        Raises:
            ValueError: If completed scores still lack current policy proof.
        """
        counts = await self.canonical_policy_pending_counts(
            stage_a_policy=stage_a_policy,
            stage_b_policy=stage_b_policy,
        )
        if counts["stage_a_pending"] or counts["stage_b_pending"]:
            detail = ", ".join(f"{key}={value}" for key, value in counts.items())
            raise ValueError(f"canonical policy cutover pending: {detail}")
        return True

    async def load_real_job_priority_inputs(
        self,
        real_job_ids: list[str],
        *,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[CanonicalPriorityInput]:
        """Return one canonical priority input per requested parent.

        Time complexity: O(N + R) Python grouping work for N distinct parents
        and R source rows. Fixed-size parent batches partition the result.

        Args:
            real_job_ids: Canonical parent IDs to load.
            stage_a_policy: Configured Stage A policy used to verify stored scores.
            stage_b_policy: Configured Stage B policy used to verify stored scores.

        Returns:
            Priority inputs for existing requested canonical jobs.
        """
        if not real_job_ids:
            return []
        ids = list(dict.fromkeys(int(value) for value in real_job_ids))
        grouped: dict[int, list[JobPosting]] = {value: [] for value in ids}
        metadata: dict[int, aiosqlite.Row] = {}
        async with self._lifecycle.connection() as connection:
            connection.row_factory = aiosqlite.Row
            for start in range(0, len(ids), 900):
                page = ids[start : start + 900]
                placeholders = ",".join("?" for _ in page)
                cursor = await connection.execute(
                    "SELECT j.*,r.representative_job_id,s.status AS real_status,"
                    "e.stage_a_status AS real_a_status,"
                    "e.stage_a_score AS real_a_score,e.stage_b_json AS real_b_json,"
                    "e.stage_b_status AS real_b_status,"
                    "e.input_facts_json AS real_input_facts_json "
                    "FROM jobs j JOIN real_jobs r ON r.id=j.real_job_id "
                    "LEFT JOIN real_job_status s ON s.real_job_id=r.id "
                    "LEFT JOIN real_job_evaluations e ON e.real_job_id=r.id "
                    f"WHERE r.id IN ({placeholders}) ORDER BY r.id,j.id",
                    page,
                )
                rows = list(await cursor.fetchall())
                await cursor.close()
                for row in rows:
                    real_id = int(row["real_job_id"])
                    grouped[real_id].append(_job_from_row(row))
                    metadata[real_id] = row
        output: list[CanonicalPriorityInput] = []
        for real_id in ids:
            if real_id not in metadata:
                continue
            row = metadata[real_id]
            stage_b = json.loads(row["real_b_json"]) if row["real_b_json"] else {}
            fit = stage_b.get("fit_analysis", {}).get("score")
            a_visible, b_visible, _ = policy_visibility(
                row["real_input_facts_json"],
                stage_a_policy=stage_a_policy,
                stage_b_policy=stage_b_policy,
            )
            item = priority_input_for_sources(
                str(real_id),
                grouped[real_id],
                representative_source_id=(
                    str(row["representative_job_id"])
                    if row["representative_job_id"] is not None
                    else None
                ),
                status=str(row["real_status"] or "new"),
                stage_a_score=(
                    int(row["real_a_score"])
                    if a_visible
                    and row["real_a_status"] == "completed"
                    and row["real_a_score"] is not None
                    else None
                ),
                stage_b_fit_score=int(fit) if b_visible and fit is not None else None,
                stage_a_status=row["real_a_status"] if a_visible else None,
                stage_b_status=row["real_b_status"] if b_visible else None,
                input_facts_json=row["real_input_facts_json"],
                now=self._now(),
            )
            if item is not None:
                output.append(item)
        return output

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load one persisted pipeline run by identity.

        Args:
            run_id: Exact pipeline UUID text.

        Returns:
            Hydrated run when present, otherwise None.
        """
        async with self._lifecycle.connection() as connection:
            return await _get_pipeline_run(connection, run_id)

    def _now(self) -> datetime:
        value = self._application_clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SQLite store clock must return an aware datetime")
        return value.astimezone(UTC)

    def _claim_time(self, value: datetime | None) -> datetime:
        return self._now() if value is None else super()._claim_time(value)

    def _application_time(self, value: datetime | None = None) -> datetime:
        if value is None:
            return self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SQLite application time must be aware")
        return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["SQLiteStore"]
