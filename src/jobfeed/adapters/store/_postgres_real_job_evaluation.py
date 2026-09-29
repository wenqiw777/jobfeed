"""PostgreSQL canonical evaluation claims and version-fenced writes."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import asyncpg  # type: ignore[import-untyped]

from jobfeed.domain.models import JobPosting, MLGateResult, StageAResult, StageBResult
from jobfeed.domain.real_job_evaluation import (
    EVALUATION_ACTIVATION_KEY,
    RealJobEvaluationInput,
    conflicting_complete_sources,
    input_facts_json,
    legacy_evaluation_input_hold,
    ml_gate_result_json,
    official_closed_at,
    replace_evaluation_policies,
    representative_source_id,
    same_evaluation_input,
    select_real_job_input,
    stage_b_result_json,
)
from jobfeed.domain.scoring import MAX_STAGE_RETRIES

_MAX_BACKFILL_PAGE = 1000


class PostgresRealJobEvaluation:
    """PostgreSQL canonical scoring claims and fenced writes."""

    def _get_pool(self) -> asyncpg.Pool:
        raise NotImplementedError

    async def claim_real_job_stage_a_by_ids(  # noqa: C901 - atomic claim state machine
        self,
        real_job_ids: list[str],
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[RealJobEvaluationInput]:
        """Atomically claim eligible parents for one Stage A evaluation each.

        Args:
            real_job_ids: Canonical parents to consider.
            limit: Maximum number of claims to return.
            max_days: Optional age limit for the selected posting.
            stage_a_policy: Explicit quick-score model and gate policy.
            stage_b_policy: Explicit detailed-score model policy.

        Returns:
            Selected inputs with revision and claim-generation fences.
        """
        if limit <= 0:
            return []
        ids = sorted({int(value) for value in real_job_ids})
        if not ids:
            return []
        now = datetime.now(UTC)
        claimed: list[RealJobEvaluationInput] = []
        async with self._get_pool().acquire() as db, db.transaction():
            for real_id in ids:
                if len(claimed) >= limit:
                    break
                state = await db.fetchrow(
                    (
                        "SELECT identity_review_state FROM real_jobs "
                        "WHERE id=$1 FOR UPDATE"
                    ),
                    real_id,
                )
                if state is None or state["identity_review_state"] != "clear":
                    continue
                rows = await db.fetch(
                    "SELECT * FROM jobs WHERE real_job_id=$1 ORDER BY id", real_id
                )
                # Import at call time to avoid a circular store-module import.
                from jobfeed.adapters.store.postgres import (  # noqa: PLC0415 - circular facade
                    _job_from_record,
                )

                selected = select_real_job_input(
                    str(real_id), [_job_from_record(row) for row in rows], now=now
                )
                if (
                    selected is None
                    or selected.job.closed_at is not None
                    or selected.job.is_repost is True
                    or _too_old(selected, now, max_days)
                ):
                    continue
                facts = input_facts_json(
                    selected,
                    stage_a_policy=stage_a_policy,
                    stage_b_policy=stage_b_policy,
                )
                current = await db.fetchrow(
                    "SELECT * FROM real_job_evaluations WHERE real_job_id=$1", real_id
                )
                if current is None:
                    revision = 1
                    generation = 1
                    await db.execute(
                        (
                            "INSERT INTO real_job_evaluations(real_job_id,"
                            "source_job_id,input_jd_text,input_facts_json,"
                            "input_revision,claim_generation,"
                            "stage_a_status,updated_at) "
                            "VALUES($1,$2,$3,$4,$5,1,'in_progress',$6)"
                        ),
                        real_id,
                        int(selected.source_job_id),
                        selected.job.jd_text,
                        facts,
                        revision,
                        now,
                    )
                elif current["stage_a_status"] == "completed":
                    # Never automatically re-evaluate completed jobs.
                    continue
                elif not same_evaluation_input(
                    current["input_jd_text"],
                    current["input_facts_json"],
                    selected,
                    stage_a_policy=stage_a_policy,
                ):
                    revision = int(current["input_revision"]) + 1
                    generation = int(current["claim_generation"]) + 1
                    reason = "input_changed"
                    await db.execute(
                        """INSERT INTO real_job_evaluation_history(
                               real_job_id,source_job_id,input_revision,input_facts_json,
                               stage_a_status,stage_a_score,stage_b_status,
                               stage_b_verdict,stage_b_json,archived_at,reason
                           ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                        real_id,
                        current["source_job_id"],
                        current["input_revision"],
                        current["input_facts_json"],
                        current["stage_a_status"],
                        current["stage_a_score"],
                        current["stage_b_status"],
                        current["stage_b_verdict"],
                        current["stage_b_json"],
                        now,
                        reason,
                    )
                    await db.execute(
                        (
                            "UPDATE real_job_evaluations SET "
                            "source_job_id=$1,input_jd_text=$2,"
                            "input_facts_json=$3,input_revision=$4,"
                            "claim_generation=claim_generation+1,"
                            "stage_a_status='in_progress',stage_a_score=NULL,"
                            "stage_a_one_line=NULL,"
                            "stage_a_timing_eligible=NULL,stage_a_model=NULL,"
                            "stage_a_cost_usd=NULL,"
                            "stage_a_at=NULL,stage_b_status=NULL,"
                            "stage_b_verdict=NULL,stage_b_json=NULL,"
                            "stage_b_model=NULL,stage_b_cost_usd=NULL,"
                            "stage_b_at=NULL,ml_gate_result=NULL,"
                            "ml_gate_score=NULL,updated_at=$5 WHERE "
                            "real_job_id=$6"
                        ),
                        int(selected.source_job_id),
                        selected.job.jd_text,
                        facts,
                        revision,
                        now,
                        real_id,
                    )
                elif (
                    current["stage_a_status"] == "error"
                    and current["stage_a_error_count"] >= MAX_STAGE_RETRIES
                ) or (
                    current["stage_a_status"] == "in_progress"
                    and (current["updated_at"] >= now - timedelta(hours=1))
                ):
                    continue
                else:
                    revision = int(current["input_revision"])
                    generation = int(current["claim_generation"]) + 1
                    await db.execute(
                        (
                            "UPDATE real_job_evaluations SET "
                            "stage_a_status='in_progress',"
                            "claim_generation=claim_generation+1,updated_at=$1 "
                            "WHERE real_job_id=$2"
                        ),
                        now,
                        real_id,
                    )
                claimed.append(
                    RealJobEvaluationInput(
                        selected.real_job_id,
                        selected.source_job_id,
                        selected.job,
                        revision,
                        ml_gate_result=(
                            current["ml_gate_result"] if current is not None else None
                        ),
                        claim_generation=generation,
                    )
                )
        return claimed

    async def save_real_job_stage_a(
        self,
        real_job_id: str,
        result: StageAResult,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Save Stage A only while this worker owns the current claim.

        Args:
            real_job_id: Canonical parent to update.
            result: Completed quick evaluation.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced write succeeded.
        """
        now = datetime.now(UTC)
        async with self._get_pool().acquire() as db, db.transaction():
            changed = await db.fetchval(
                (
                    "UPDATE real_job_evaluations SET "
                    "stage_a_status='completed',stage_a_score=$1,"
                    "stage_a_one_line=$2,stage_a_timing_eligible=$3,"
                    "stage_a_model=$4,stage_a_cost_usd=$5,"
                    "stage_a_at=$6,updated_at=$6 WHERE "
                    "real_job_id=$7 AND input_revision=$8 AND claim_generation=$9 AND "
                    "stage_a_status='in_progress' RETURNING "
                    "real_job_id"
                ),
                result.score,
                result.one_line,
                result.timing_eligible,
                result.model,
                result.cost_usd,
                now,
                int(real_job_id),
                expected_revision,
                expected_generation,
            )
            if changed is None:
                return False
            advanced = await db.fetchval(
                (
                    "UPDATE real_job_status SET status='scored',"
                    "last_status_change_at=$1 WHERE real_job_id=$2 "
                    "AND status='new' RETURNING real_job_id"
                ),
                now,
                int(real_job_id),
            )
            if advanced is not None:
                await db.execute(
                    (
                        "INSERT INTO real_job_status_history(real_job_id,"
                        "from_status,to_status,changed_at,reason) "
                        "VALUES($1,'new','scored',$2,'auto_scored')"
                    ),
                    int(real_job_id),
                    now,
                )
            return True

    async def save_real_job_ml_gate(
        self,
        real_job_id: str,
        result: MLGateResult,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Save gate evidence only for the current Stage A owner.

        Args:
            real_job_id: Canonical parent to update.
            result: Gate decision and evidence.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced write succeeded.
        """
        async with self._get_pool().acquire() as db:
            changed = await db.fetchval(
                """UPDATE real_job_evaluations SET
                       ml_gate_result=$1,ml_gate_score=$2,ml_gate_json=$3,updated_at=$4
                   WHERE real_job_id=$5 AND input_revision=$6 AND claim_generation=$7
                     AND stage_a_status='in_progress' RETURNING real_job_id""",
                result.result,
                result.score,
                ml_gate_result_json(result),
                datetime.now(UTC),
                int(real_job_id),
                expected_revision,
                expected_generation,
            )
            return changed is not None

    async def claim_real_job_stage_b_by_ids(  # noqa: PLR0913 - atomic policy claim
        self,
        real_job_ids: list[str],
        *,
        stage_a_threshold: int,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> list[RealJobEvaluationInput]:
        """Claim eligible scored parents for detailed evaluation.

        Args:
            real_job_ids: Canonical parents to consider.
            stage_a_threshold: Minimum Stage A score.
            limit: Maximum number of claims to return.
            max_days: Optional age limit for the selected posting.
            stage_a_policy: Quick-score policy required by this claim.
            stage_b_policy: Detailed-score policy to apply.

        Returns:
            Selected inputs with current Stage A scores and claim fences.
        """
        if limit <= 0:
            return []
        now = datetime.now(UTC)
        claimed: list[RealJobEvaluationInput] = []
        async with self._get_pool().acquire() as db, db.transaction():
            for real_id in sorted({int(value) for value in real_job_ids}):
                if len(claimed) >= limit:
                    break
                state = await db.fetchrow(
                    "SELECT identity_review_state FROM real_jobs "
                    "WHERE id=$1 FOR UPDATE",
                    real_id,
                )
                if state is None or state["identity_review_state"] != "clear":
                    continue
                current = await db.fetchrow(
                    "SELECT * FROM real_job_evaluations WHERE real_job_id=$1", real_id
                )
                if (
                    current is None
                    or current["stage_a_status"] != "completed"
                    or current["stage_a_score"] < stage_a_threshold
                ):
                    continue
                rows = await db.fetch(
                    "SELECT * FROM jobs WHERE real_job_id=$1 ORDER BY id", real_id
                )
                from jobfeed.adapters.store.postgres import (  # noqa: PLC0415 - circular facade
                    _job_from_record,
                )

                selected = select_real_job_input(
                    str(real_id), [_job_from_record(row) for row in rows], now=now
                )
                if (
                    selected is None
                    or selected.job.closed_at is not None
                    or selected.job.is_repost is True
                    or _too_old(selected, now, max_days)
                ):
                    continue
                if not same_evaluation_input(
                    current["input_jd_text"],
                    current["input_facts_json"],
                    selected,
                    stage_a_policy=stage_a_policy,
                ):
                    continue
                if (
                    current["stage_b_status"] == "completed"
                    or (
                        current["stage_b_status"] == "error"
                        and current["stage_b_error_count"] >= MAX_STAGE_RETRIES
                    )
                    or (
                        current["stage_b_status"] == "in_progress"
                        and current["updated_at"] >= now - timedelta(hours=1)
                    )
                ):
                    continue
                await db.execute(
                    (
                        "UPDATE real_job_evaluations SET "
                        "stage_b_status='in_progress',input_facts_json=$3,"
                        "claim_generation=claim_generation+1,updated_at=$1 "
                        "WHERE real_job_id=$2"
                    ),
                    now,
                    real_id,
                    replace_evaluation_policies(
                        current["input_facts_json"], stage_b_policy=stage_b_policy
                    ),
                )
                claimed.append(
                    RealJobEvaluationInput(
                        selected.real_job_id,
                        selected.source_job_id,
                        selected.job,
                        int(current["input_revision"]),
                        int(current["stage_a_score"]),
                        claim_generation=int(current["claim_generation"]) + 1,
                    )
                )
        return claimed

    async def save_real_job_stage_b(
        self,
        real_job_id: str,
        result: StageBResult,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Save the detailed answer only for the current Stage B owner.

        Args:
            real_job_id: Canonical parent to update.
            result: Completed detailed evaluation.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced write succeeded.
        """
        now = datetime.now(UTC)
        async with self._get_pool().acquire() as db, db.transaction():
            changed = await db.fetchval(
                (
                    "UPDATE real_job_evaluations SET "
                    "stage_b_status='completed',stage_b_verdict=$1,"
                    "stage_b_json=$2,stage_b_model=$3,"
                    "stage_b_cost_usd=$4,stage_b_at=$5,updated_at=$5 "
                    "WHERE real_job_id=$6 AND input_revision=$7 "
                    "AND claim_generation=$8 AND "
                    "stage_b_status='in_progress' RETURNING "
                    "real_job_id"
                ),
                result.verdict.value,
                stage_b_result_json(result),
                result.model,
                result.cost_usd,
                now,
                int(real_job_id),
                expected_revision,
                expected_generation,
            )
            return changed is not None

    async def refresh_real_job_stage_a_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Extend a still-owned Stage A lease.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker still owns the claim.
        """
        return await self._refresh_real_job_claim(
            real_job_id,
            stage="a",
            expected_revision=expected_revision,
            expected_generation=expected_generation,
        )

    async def refresh_real_job_stage_b_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Extend a still-owned Stage B lease.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker still owns the claim.
        """
        return await self._refresh_real_job_claim(
            real_job_id,
            stage="b",
            expected_revision=expected_revision,
            expected_generation=expected_generation,
        )

    async def _refresh_real_job_claim(
        self,
        real_job_id: str,
        *,
        stage: str,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        async with self._get_pool().acquire() as db:
            changed = await db.fetchval(
                "UPDATE real_job_evaluations SET updated_at=$1 WHERE real_job_id=$2 "
                "AND input_revision=$3 AND claim_generation=$4 "
                f"AND stage_{stage}_status='in_progress' "
                "RETURNING real_job_id",
                datetime.now(UTC),
                int(real_job_id),
                expected_revision,
                expected_generation,
            )
            return changed is not None

    async def save_real_job_stage_a_error(
        self,
        real_job_id: str,
        error: str,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Record a Stage A failure only for the current claim owner.

        Args:
            real_job_id: Claimed canonical parent.
            error: Failure message to retain.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced update succeeded.
        """
        return await self._finish_real_job_error(
            real_job_id,
            error,
            expected_revision=expected_revision,
            expected_generation=expected_generation,
            stage="a",
        )

    async def save_real_job_stage_b_error(
        self,
        real_job_id: str,
        error: str,
        *,
        expected_revision: int,
        expected_generation: int,
    ) -> bool:
        """Record a Stage B failure only for the current claim owner.

        Args:
            real_job_id: Claimed canonical parent.
            error: Failure message to retain.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether the fenced update succeeded.
        """
        return await self._finish_real_job_error(
            real_job_id,
            error,
            expected_revision=expected_revision,
            expected_generation=expected_generation,
            stage="b",
        )

    async def release_real_job_stage_a_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Release a still-owned Stage A claim without recording an error.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker released its claim.
        """
        return await self._finish_real_job_error(
            real_job_id,
            None,
            expected_revision=expected_revision,
            expected_generation=expected_generation,
            stage="a",
        )

    async def release_real_job_stage_b_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool:
        """Release a still-owned Stage B claim without recording an error.

        Args:
            real_job_id: Claimed canonical parent.
            expected_revision: Claimed input revision.
            expected_generation: Claimed lease generation.

        Returns:
            Whether this worker released its claim.
        """
        return await self._finish_real_job_error(
            real_job_id,
            None,
            expected_revision=expected_revision,
            expected_generation=expected_generation,
            stage="b",
        )

    async def _finish_real_job_error(
        self,
        real_job_id: str,
        error: str | None,
        *,
        expected_revision: int,
        expected_generation: int,
        stage: str,
    ) -> bool:
        status = "error" if error is not None else None
        count = f"stage_{stage}_error_count"
        async with self._get_pool().acquire() as db:
            changed = await db.fetchval(
                (
                    f"UPDATE real_job_evaluations SET stage_{stage}_status=$1,"
                    f"stage_{stage}_error=$2,{count}={count}+$3,updated_at=$4 "
                    "WHERE real_job_id=$5 AND input_revision=$6 "
                    "AND claim_generation=$7 "
                    f"AND stage_{stage}_status='in_progress' RETURNING real_job_id"
                ),
                status,
                error,
                int(error is not None),
                datetime.now(UTC),
                int(real_job_id),
                expected_revision,
                expected_generation,
            )
            return changed is not None

    async def backfill_real_job_evaluations(
        self, *, after_id: int = 0, limit: int = 100
    ) -> tuple[int, int]:
        """Copy only verified old scores and hold uncertain parents.

        Args:
            after_id: Last processed real-job ID.
            limit: Maximum parents to inspect in this page.

        Returns:
            Last inspected ID and number of scores safely copied.

        Raises:
            ValueError: If the requested page size is outside the supported range.
        """
        if limit < 1 or limit > _MAX_BACKFILL_PAGE:
            raise ValueError("limit must be between 1 and 1000")
        from jobfeed.adapters.store.postgres import (  # noqa: PLC0415 - circular facade
            _job_from_record,
        )

        copied = 0
        async with self._get_pool().acquire() as db, db.transaction():
            parents = await db.fetch(
                "SELECT id,identity_review_state FROM real_jobs "
                "WHERE id>$1 ORDER BY id LIMIT $2 FOR UPDATE",
                after_id,
                limit,
            )
            for parent in parents:
                real_id = int(parent["id"])
                if await db.fetchval(
                    "SELECT 1 FROM real_job_evaluations WHERE real_job_id=$1", real_id
                ):
                    continue
                if parent["identity_review_state"] != "clear":
                    continue
                source_rows = await db.fetch(
                    "SELECT * FROM jobs WHERE real_job_id=$1 ORDER BY id", real_id
                )
                if await _adopt_postgres_legacy_score(
                    db, real_id, [_job_from_record(row) for row in source_rows]
                ):
                    copied += 1
        return (int(parents[-1]["id"]) if parents else after_id, copied)


async def _adopt_postgres_legacy_score(
    db: asyncpg.Connection, real_id: int, jobs: list[JobPosting]
) -> bool:
    """Reuse one historical answer with explicit source/revision provenance."""
    evaluations = await db.fetch(
        "SELECT e.* FROM evaluations e JOIN jobs j ON j.id=e.job_id "
        "WHERE j.real_job_id=$1 AND e.stage_a_status='completed' "
        "ORDER BY e.job_id",
        real_id,
    )
    if not evaluations:
        return False
    selected = select_real_job_input(str(real_id), jobs, now=datetime.now(UTC))
    hold = legacy_evaluation_input_hold(
        selected,
        jobs,
        [(int(row["job_id"]), row["stage_a_at"]) for row in evaluations],
    )
    if hold is not None:
        await db.execute(
            "UPDATE real_jobs SET identity_review_state=$1 WHERE id=$2",
            hold,
            real_id,
        )
        return False
    assert selected is not None
    chosen = max(
        evaluations,
        key=lambda row: (
            row["stage_a_score"] if row["stage_a_score"] is not None else -1,
            row["stage_a_at"] or datetime.min.replace(tzinfo=UTC),
            int(row["job_id"]),
        ),
    )
    now = datetime.now(UTC)
    await db.execute(
        """INSERT INTO real_job_evaluations(
               real_job_id,source_job_id,input_jd_text,
               input_facts_json,input_revision,stage_a_status,
               stage_a_score,stage_a_one_line,
               stage_a_timing_eligible,stage_a_model,
               stage_a_cost_usd,stage_a_at,stage_b_status,
               stage_b_verdict,stage_b_json,stage_b_model,
               stage_b_cost_usd,stage_b_at,updated_at)
           VALUES($1,$2,$3,$4,1,'completed',$5,$6,$7,$8,$9,$10,
                  $11,$12,$13,$14,$15,$16,$17)""",
        real_id,
        int(selected.source_job_id),
        selected.job.jd_text,
        input_facts_json(selected),
        chosen["stage_a_score"],
        chosen["stage_a_one_line"],
        chosen["stage_a_timing_eligible"],
        chosen["stage_a_model"],
        chosen["stage_a_cost_usd"],
        chosen["stage_a_at"],
        chosen["stage_b_status"],
        chosen["stage_b_verdict"],
        _legacy_stage_b_json(chosen),
        chosen["stage_b_model"],
        chosen["stage_b_cost_usd"],
        chosen["stage_b_at"],
        now,
    )
    await db.execute(
        "INSERT INTO state(key,value) VALUES($1,$2) ON CONFLICT(key) "
        "DO UPDATE SET value=excluded.value",
        f"real-job-legacy-adoption:{real_id}:1",
        json.dumps(
            {
                "reason": "user_approved_legacy_score_reuse",
                "input_revision": 1,
                "score_source_job_id": int(chosen["job_id"]),
                "input_source_job_id": int(selected.source_job_id),
                "stage_a_score": chosen["stage_a_score"],
                "adopted_at": now.isoformat(),
            },
            sort_keys=True,
        ),
    )
    advanced = await db.fetchval(
        (
            "UPDATE real_job_status SET status='scored',"
            "last_status_change_at=$1 WHERE real_job_id=$2 "
            "AND status='new' RETURNING real_job_id"
        ),
        now,
        real_id,
    )
    if advanced is not None:
        await db.execute(
            (
                "INSERT INTO real_job_status_history(real_job_id,"
                "from_status,to_status,changed_at,reason) "
                "VALUES($1,'new','scored',$2,"
                "'legacy_evaluation_backfill')"
            ),
            real_id,
            now,
        )
    return True


async def _resolve_postgres_requirements_hold(
    db: asyncpg.Connection, real_id: int, jobs: list[JobPosting]
) -> None:
    """Clear a corrected JD hold only if no other review conflict survives."""
    state = await db.fetchval(
        "SELECT identity_review_state FROM real_jobs WHERE id=$1", real_id
    )
    if state != "requirements_conflict":
        return
    other_review = await db.fetchval(
        "SELECT 1 FROM real_job_review_cases WHERE "
        "left_real_job_id=$1 OR right_real_job_id=$1 LIMIT 1",
        real_id,
    )
    status_count = await db.fetchval(
        "SELECT COUNT(DISTINCT status) FROM ("
        "SELECT s.status FROM job_status s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.real_job_id=$1 AND s.status NOT IN ('new','scored') "
        "UNION ALL SELECT status FROM real_job_status WHERE real_job_id=$1 "
        "AND status NOT IN ('new','scored')) statuses",
        real_id,
    )
    if (
        other_review is None
        and status_count <= 1
        and select_real_job_input(str(real_id), jobs, now=datetime.now(UTC)) is not None
    ):
        await db.execute(
            "UPDATE real_jobs SET identity_review_state='clear' WHERE id=$1",
            real_id,
        )


async def sync_postgres_real_job_input(  # noqa: C901 - atomic source synchronization
    db: asyncpg.Connection,
    source_id: int,
    hydrate: Callable[[asyncpg.Record], JobPosting],
) -> None:
    """Archive a changed canonical input within its source write.

    Args:
        db: Open source-write transaction.
        source_id: Source posting whose input may have changed.
        hydrate: Converter from database record to source posting.
    """
    if not await db.fetchval(
        "SELECT to_regclass('public.real_job_evaluations') IS NOT NULL"
    ):
        return
    real_id = await db.fetchval("SELECT real_job_id FROM jobs WHERE id=$1", source_id)
    if real_id is None:
        return
    rows = await db.fetch(
        "SELECT * FROM jobs WHERE real_job_id=$1 ORDER BY id", real_id
    )
    jobs = [hydrate(row) for row in rows]
    await db.execute(
        "UPDATE real_jobs SET representative_job_id=$1,official_closed_at=$2 "
        "WHERE id=$3",
        representative_source_id(str(real_id), jobs),
        official_closed_at(jobs),
        real_id,
    )
    if conflict := conflicting_complete_sources(jobs):
        # Existing source scores need the backfill's more specific evaluation hold.
        old_score = await db.fetchval(
            "SELECT 1 FROM evaluations e JOIN jobs j ON j.id=e.job_id "
            "WHERE j.real_job_id=$1 AND e.stage_a_status='completed' LIMIT 1",
            real_id,
        )
        if old_score is None:
            await db.execute(
                "UPDATE real_jobs SET identity_review_state='requirements_conflict' "
                "WHERE id=$1 AND identity_review_state='clear'",
                real_id,
            )
        await db.execute(
            "INSERT INTO real_job_review_cases("
            "left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason) "
            "SELECT $1,$1,$2,$3,'requirements_conflict' WHERE NOT EXISTS("
            "SELECT 1 FROM real_job_review_cases WHERE left_real_job_id=$1 "
            "AND right_real_job_id=$1 AND reason='requirements_conflict')",
            real_id,
            *conflict,
        )
    else:
        await db.execute(
            "DELETE FROM real_job_review_cases WHERE left_real_job_id=$1 "
            "AND right_real_job_id=$1 AND reason='requirements_conflict'",
            real_id,
        )
        await _resolve_postgres_requirements_hold(db, real_id, jobs)
    current = await db.fetchrow(
        "SELECT * FROM real_job_evaluations WHERE real_job_id=$1 FOR UPDATE", real_id
    )
    if current is None:
        activation = await db.fetchval(
            "SELECT value FROM state WHERE key=$1", EVALUATION_ACTIVATION_KEY
        )
        review_state = await db.fetchval(
            "SELECT identity_review_state FROM real_jobs WHERE id=$1", real_id
        )
        if activation == "enabled" and review_state == "clear":
            await _adopt_postgres_legacy_score(db, real_id, jobs)
        return
    if current["stage_a_status"] == "completed" or (
        current["stage_a_status"] is None and current["stage_b_status"] is None
    ):
        # Keep the paid result and its original input as an audit snapshot.
        return
    selected = select_real_job_input(str(real_id), jobs, now=datetime.now(UTC))
    if selected is not None:
        stored_policy = json.loads(current["input_facts_json"])
        facts = input_facts_json(
            selected,
            stage_a_policy=stored_policy.get("stage_a_policy"),
            stage_b_policy=stored_policy.get("stage_b_policy"),
        )
        if same_evaluation_input(
            current["input_jd_text"], current["input_facts_json"], selected
        ):
            await db.execute(
                "UPDATE real_job_evaluations SET source_job_id=$1,input_jd_text=$2 "
                "WHERE real_job_id=$3",
                int(selected.source_job_id),
                selected.job.jd_text,
                real_id,
            )
            return
    now = datetime.now(UTC)
    await db.execute(
        """INSERT INTO real_job_evaluation_history(
               real_job_id,source_job_id,input_revision,input_facts_json,stage_a_status,
               stage_a_score,stage_b_status,stage_b_verdict,stage_b_json,
               archived_at,reason)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'input_changed')""",
        real_id,
        current["source_job_id"],
        current["input_revision"],
        current["input_facts_json"],
        current["stage_a_status"],
        current["stage_a_score"],
        current["stage_b_status"],
        current["stage_b_verdict"],
        current["stage_b_json"],
        now,
    )
    if selected is None:
        await db.execute(
            (
                "UPDATE real_jobs SET identity_review_state='requ"
                "irements_conflict' WHERE id=$1"
            ),
            real_id,
        )
        source_job_id = current["source_job_id"]
        jd_text = current["input_jd_text"]
        facts = current["input_facts_json"]
    else:
        source_job_id = int(selected.source_job_id)
        jd_text = selected.job.jd_text
    await db.execute(
        """UPDATE real_job_evaluations SET
               source_job_id=$1,input_jd_text=$2,input_facts_json=$3,
               input_revision=input_revision+1,stage_a_status=NULL,
               stage_a_score=NULL,stage_a_one_line=NULL,
               stage_a_timing_eligible=NULL,stage_a_model=NULL,
               stage_a_cost_usd=NULL,stage_a_at=NULL,stage_b_status=NULL,
               stage_b_verdict=NULL,stage_b_json=NULL,stage_b_model=NULL,
               stage_b_cost_usd=NULL,stage_b_at=NULL,ml_gate_result=NULL,
               ml_gate_score=NULL,ml_gate_json=NULL,updated_at=$4
           WHERE real_job_id=$5""",
        source_job_id,
        jd_text,
        facts,
        now,
        real_id,
    )


def _too_old(item: RealJobEvaluationInput, now: datetime, max_days: int | None) -> bool:
    if max_days is None:
        return False
    effective = item.job.posted_at
    if effective is None or effective > now:
        effective = item.job.discovered_at
    return effective < now - timedelta(days=max_days)


def _legacy_stage_b_json(row: asyncpg.Record) -> str | None:
    if row["stage_b_status"] != "completed":
        return None
    fit = row["stage_b_fit_json"] or {}
    if isinstance(fit, str):
        fit = json.loads(fit)
    hooks = row["stage_b_hooks_json"] or {}
    if isinstance(hooks, str):
        hooks = json.loads(hooks)
    return json.dumps(
        {
            "verdict": row["stage_b_verdict"],
            "jd_summary": row["stage_b_jd_summary"],
            "fit_analysis": {"score": fit.get("score_0_100"), **fit},
            "resume_hooks": hooks,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
