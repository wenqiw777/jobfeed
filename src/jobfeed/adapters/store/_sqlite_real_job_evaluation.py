"""SQLite canonical evaluation claims; source evaluations remain audit rows."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import _immediate_transaction
from jobfeed.adapters.store._sqlite_values import (
    _canonical_json,
    _datetime_from_text,
    _job_from_row,
    _utc_text,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import (
    JobPosting,
    MLGateResult,
    QualityBand,
    StageAResult,
    StageBResult,
)
from jobfeed.domain.real_job_evaluation import (
    EVALUATION_ACTIVATION_KEY,
    RealJobEvaluationInput,
    canonical_sort_dates,
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
_REVIEW_SOURCE_KEY = "real-job-evaluation-source:"
_COMPLETE_QUALITY = {QualityBand.FULL, QualityBand.GOOD}


class SqliteRealJobEvaluation:
    """Atomic claims and version-fenced writes by real-job identity."""

    _lifecycle: SqliteLifecycle

    def _now(self) -> datetime:
        """Require the concrete store to provide its UTC clock."""
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
        ids = list(dict.fromkeys(int(value) for value in real_job_ids))
        if not ids:
            return []
        now = self._now()
        timestamp = _utc_text(now)
        claimed: list[RealJobEvaluationInput] = []
        async with self._lifecycle.connection() as connection:
            for real_id in ids:
                if len(claimed) >= limit:
                    break
                # Release the writer between candidates, including skipped ones.
                # Every input/version check and its claim remain atomic together.
                async with (
                    _release_unreturned_claims(self, claimed, "a"),
                    _immediate_transaction(connection),
                ):
                    connection.row_factory = aiosqlite.Row
                    state = await _one(
                        connection,
                        "SELECT identity_review_state FROM real_jobs WHERE id=?",
                        (real_id,),
                    )
                    if state is None or state["identity_review_state"] != "clear":
                        continue
                    rows = await _all(
                        connection,
                        "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id",
                        (real_id,),
                    )
                    selected = await _select_sqlite_real_job_input(
                        connection,
                        real_id,
                        [_job_from_row(row) for row in rows],
                        now=now,
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
                    current = await _one(
                        connection,
                        "SELECT * FROM real_job_evaluations WHERE real_job_id=?",
                        (real_id,),
                    )
                    if current is None:
                        revision = 1
                        generation = 1
                        await connection.execute(
                            (
                                "INSERT INTO real_job_evaluations(real_job_id,"
                                "source_job_id,input_jd_text,input_facts_json,"
                                "input_revision,claim_generation,"
                                "stage_a_status,updated_at) "
                                "VALUES(?,?,?,?,?,1,'in_progress',?)"
                            ),
                            (
                                real_id,
                                int(selected.source_job_id),
                                selected.job.jd_text,
                                facts,
                                revision,
                                timestamp,
                            ),
                        )
                    elif not same_evaluation_input(
                        current["input_jd_text"],
                        current["input_facts_json"],
                        selected,
                        stage_a_policy=stage_a_policy,
                    ):
                        revision = int(current["input_revision"]) + 1
                        generation = int(current["claim_generation"]) + 1
                        reason = (
                            "policy_changed"
                            if same_evaluation_input(
                                current["input_jd_text"],
                                current["input_facts_json"],
                                selected,
                            )
                            else "input_changed"
                        )
                        await connection.execute(
                            """INSERT INTO real_job_evaluation_history(
                                   real_job_id,source_job_id,input_revision,input_facts_json,
                                   stage_a_status,stage_a_score,stage_b_status,
                                   stage_b_verdict,stage_b_json,archived_at,reason
                               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                real_id,
                                current["source_job_id"],
                                current["input_revision"],
                                current["input_facts_json"],
                                current["stage_a_status"],
                                current["stage_a_score"],
                                current["stage_b_status"],
                                current["stage_b_verdict"],
                                current["stage_b_json"],
                                timestamp,
                                reason,
                            ),
                        )
                        await connection.execute(
                            (
                                "UPDATE real_job_evaluations SET source_job_id=?,"
                                "input_jd_text=?,input_facts_json=?,"
                                "input_revision=?,claim_generation=claim_generation+1,"
                                "stage_a_status='in_progress',"
                                "stage_a_score=NULL,stage_a_one_line=NULL,"
                                "stage_a_timing_eligible=NULL,stage_a_model=NULL,"
                                "stage_a_cost_usd=NULL,stage_a_at=NULL,"
                                "stage_b_status=NULL,stage_b_verdict=NULL,"
                                "stage_b_json=NULL,stage_b_model=NULL,"
                                "stage_b_cost_usd=NULL,stage_b_at=NULL,"
                                "ml_gate_result=NULL,ml_gate_score=NULL,"
                                "updated_at=? WHERE real_job_id=?"
                            ),
                            (
                                int(selected.source_job_id),
                                selected.job.jd_text,
                                facts,
                                revision,
                                timestamp,
                                real_id,
                            ),
                        )
                    elif current["stage_a_status"] == "completed":
                        await connection.execute(
                            (
                                "UPDATE real_job_evaluations SET source_job_id=?,"
                                "input_jd_text=? WHERE real_job_id=?"
                            ),
                            (
                                int(selected.source_job_id),
                                selected.job.jd_text,
                                real_id,
                            ),
                        )
                        continue
                    elif (
                        current["stage_a_status"] == "error"
                        and current["stage_a_error_count"] >= MAX_STAGE_RETRIES
                    ) or (
                        current["stage_a_status"] == "in_progress"
                        and (
                            _required_updated_at(current["updated_at"])
                            >= now - timedelta(hours=1)
                        )
                    ):
                        continue
                    else:
                        revision = int(current["input_revision"])
                        generation = int(current["claim_generation"]) + 1
                        await connection.execute(
                            (
                                "UPDATE real_job_evaluations SET "
                                "stage_a_status='in_progress',"
                                "claim_generation=claim_generation+1,"
                                "updated_at=? WHERE "
                                "real_job_id=?"
                            ),
                            (timestamp, real_id),
                        )
                    claimed.append(
                        RealJobEvaluationInput(
                            selected.real_job_id,
                            selected.source_job_id,
                            selected.job,
                            revision,
                            ml_gate_result=(
                                current["ml_gate_result"]
                                if current is not None
                                else None
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
        now = _utc_text(self._now())
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            cursor = await connection.execute(
                (
                    "UPDATE real_job_evaluations SET "
                    "stage_a_status='completed',stage_a_score=?,"
                    "stage_a_one_line=?,stage_a_timing_eligible=?,"
                    "stage_a_model=?,stage_a_cost_usd=?,stage_a_at=?,"
                    "updated_at=? WHERE real_job_id=? AND "
                    "input_revision=? AND claim_generation=? "
                    "AND stage_a_status='in_progress'"
                ),
                (
                    result.score,
                    result.one_line,
                    result.timing_eligible,
                    result.model,
                    result.cost_usd,
                    now,
                    now,
                    int(real_job_id),
                    expected_revision,
                    expected_generation,
                ),
            )
            changed = cursor.rowcount == 1
            await cursor.close()
            if changed:
                cursor = await connection.execute(
                    (
                        "UPDATE real_job_status SET status='scored',"
                        "last_status_change_at=? WHERE real_job_id=? AND "
                        "status='new'"
                    ),
                    (now, int(real_job_id)),
                )
                status_changed = cursor.rowcount == 1
                await cursor.close()
                if status_changed:
                    await connection.execute(
                        (
                            "INSERT INTO real_job_status_history(real_job_id,"
                            "from_status,to_status,changed_at,reason) "
                            "VALUES(?,'new','scored',?,'auto_scored')"
                        ),
                        (int(real_job_id), now),
                    )
            return changed

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
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                """UPDATE real_job_evaluations SET
                       ml_gate_result=?,ml_gate_score=?,ml_gate_json=?,updated_at=?
                   WHERE real_job_id=? AND input_revision=? AND claim_generation=?
                     AND stage_a_status='in_progress'""",
                (
                    result.result,
                    result.score,
                    ml_gate_result_json(result),
                    _utc_text(self._now()),
                    int(real_job_id),
                    expected_revision,
                    expected_generation,
                ),
            )
            changed = cursor.rowcount == 1
            await cursor.close()
            return changed

    async def claim_real_job_stage_b_by_ids(  # noqa: C901, PLR0913 - atomic policy claim
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
        claimed: list[RealJobEvaluationInput] = []
        now = self._now()
        timestamp = _utc_text(now)
        for real_id in sorted({int(value) for value in real_job_ids}):
            if len(claimed) >= limit:
                break
            async with (
                _release_unreturned_claims(self, claimed, "b"),
                self._lifecycle.connection() as connection,
                _immediate_transaction(connection),
            ):
                connection.row_factory = aiosqlite.Row
                state = await _one(
                    connection,
                    "SELECT identity_review_state FROM real_jobs WHERE id=?",
                    (real_id,),
                )
                if state is None or state["identity_review_state"] != "clear":
                    continue
                current = await _one(
                    connection,
                    "SELECT * FROM real_job_evaluations WHERE real_job_id=?",
                    (real_id,),
                )
                if (
                    current is None
                    or current["stage_a_status"] != "completed"
                    or current["stage_a_score"] < stage_a_threshold
                ):
                    continue
                rows = await _all(
                    connection,
                    "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id",
                    (real_id,),
                )
                selected = await _select_sqlite_real_job_input(
                    connection,
                    real_id,
                    [_job_from_row(row) for row in rows],
                    now=now,
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
                    stage_b_policy is not None
                    and json.loads(current["input_facts_json"]).get("stage_b_policy")
                    != stage_b_policy
                ):
                    if current["stage_b_status"] is not None:
                        await connection.execute(
                            """INSERT INTO real_job_evaluation_history(
                                   real_job_id,source_job_id,input_revision,input_facts_json,
                                   stage_a_status,stage_a_score,stage_b_status,
                                   stage_b_verdict,stage_b_json,archived_at,reason)
                               VALUES(?,?,?,?,?,?,?,?,?,?,'policy_changed')""",
                            (
                                real_id,
                                current["source_job_id"],
                                current["input_revision"],
                                current["input_facts_json"],
                                current["stage_a_status"],
                                current["stage_a_score"],
                                current["stage_b_status"],
                                current["stage_b_verdict"],
                                current["stage_b_json"],
                                timestamp,
                            ),
                        )
                    await connection.execute(
                        "UPDATE real_job_evaluations SET input_facts_json=?,"
                        "claim_generation=claim_generation+1,stage_b_status=NULL,"
                        "stage_b_verdict=NULL,stage_b_json=NULL,stage_b_model=NULL,"
                        "stage_b_cost_usd=NULL,stage_b_at=NULL,updated_at=? "
                        "WHERE real_job_id=?",
                        (
                            replace_evaluation_policies(
                                current["input_facts_json"],
                                stage_b_policy=stage_b_policy,
                            ),
                            timestamp,
                            real_id,
                        ),
                    )
                    current = await _one(
                        connection,
                        "SELECT * FROM real_job_evaluations WHERE real_job_id=?",
                        (real_id,),
                    )
                assert current is not None
                if (
                    current["stage_b_status"] == "completed"
                    or (
                        current["stage_b_status"] == "error"
                        and current["stage_b_error_count"] >= MAX_STAGE_RETRIES
                    )
                    or (
                        current["stage_b_status"] == "in_progress"
                        and _required_updated_at(current["updated_at"])
                        >= now - timedelta(hours=1)
                    )
                ):
                    continue
                await connection.execute(
                    (
                        "UPDATE real_job_evaluations SET "
                        "stage_b_status='in_progress',"
                        "claim_generation=claim_generation+1,updated_at=? WHERE "
                        "real_job_id=?"
                    ),
                    (timestamp, real_id),
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
        now = _utc_text(self._now())
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            cursor = await connection.execute(
                (
                    "UPDATE real_job_evaluations SET "
                    "stage_b_status='completed',stage_b_verdict=?,"
                    "stage_b_json=?,stage_b_model=?,"
                    "stage_b_cost_usd=?,stage_b_at=?,updated_at=? "
                    "WHERE real_job_id=? AND input_revision=? "
                    "AND claim_generation=? AND "
                    "stage_b_status='in_progress'"
                ),
                (
                    result.verdict.value,
                    stage_b_result_json(result),
                    result.model,
                    result.cost_usd,
                    now,
                    now,
                    int(real_job_id),
                    expected_revision,
                    expected_generation,
                ),
            )
            changed = cursor.rowcount == 1
            await cursor.close()
            return changed

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
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "UPDATE real_job_evaluations SET updated_at=? WHERE real_job_id=? "
                "AND input_revision=? AND claim_generation=? "
                f"AND stage_{stage}_status='in_progress'",
                (
                    _utc_text(self._now()),
                    int(real_job_id),
                    expected_revision,
                    expected_generation,
                ),
            )
            changed = cursor.rowcount == 1
            await cursor.close()
            return changed

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
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                (
                    f"UPDATE real_job_evaluations SET stage_{stage}_status=?,"
                    f"stage_{stage}_error=?,{count}={count}+?,updated_at=? "
                    "WHERE real_job_id=? AND input_revision=? AND claim_generation=? "
                    f"AND stage_{stage}_status='in_progress'"
                ),
                (
                    status,
                    error,
                    int(error is not None),
                    _utc_text(self._now()),
                    int(real_job_id),
                    expected_revision,
                    expected_generation,
                ),
            )
            changed = cursor.rowcount == 1
            await cursor.close()
            return changed

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
        copied = 0
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            connection.row_factory = aiosqlite.Row
            parents = await _all(
                connection,
                "SELECT id,identity_review_state FROM real_jobs "
                "WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            )
            for parent in parents:
                real_id = int(parent["id"])
                current = await _one(
                    connection,
                    "SELECT 1 FROM real_job_evaluations WHERE real_job_id=?",
                    (real_id,),
                )
                if current is not None:
                    continue
                if parent["identity_review_state"] != "clear":
                    continue
                sources = await _all(
                    connection,
                    "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id",
                    (real_id,),
                )
                evaluations = await _all(
                    connection,
                    "SELECT e.* FROM evaluations e JOIN jobs j ON j.id=e.job_id "
                    "WHERE j.real_job_id=? AND e.stage_a_status='completed' "
                    "ORDER BY e.job_id",
                    (real_id,),
                )
                if not evaluations:
                    continue
                jobs = [_job_from_row(row) for row in sources]
                selected = await _select_sqlite_real_job_input(
                    connection, real_id, jobs, now=self._now()
                )
                variants = {
                    (
                        row["stage_a_score"],
                        row["stage_a_one_line"],
                        row["stage_a_timing_eligible"],
                        row["stage_a_model"],
                        row["stage_b_status"],
                        _legacy_stage_b_json(row),
                    )
                    for row in evaluations
                }
                if len(variants) > 1:
                    await connection.execute(
                        (
                            "UPDATE real_jobs SET "
                            "identity_review_state='evaluation_conflict' WHERE id=?"
                        ),
                        (real_id,),
                    )
                    continue
                hold = legacy_evaluation_input_hold(
                    selected,
                    jobs,
                    [
                        (int(row["job_id"]), _datetime_from_text(row["stage_a_at"]))
                        for row in evaluations
                    ],
                )
                if hold is not None:
                    await connection.execute(
                        "UPDATE real_jobs SET identity_review_state=? WHERE id=?",
                        (hold, real_id),
                    )
                    continue
                assert selected is not None
                chosen = next(
                    (
                        row
                        for row in evaluations
                        if row["job_id"] == int(selected.source_job_id)
                    ),
                    evaluations[0],
                )
                await connection.execute(
                    """INSERT INTO real_job_evaluations(
                           real_job_id,source_job_id,input_jd_text,
                           input_facts_json,input_revision,stage_a_status,
                           stage_a_score,stage_a_one_line,
                           stage_a_timing_eligible,stage_a_model,
                           stage_a_cost_usd,stage_a_at,stage_b_status,
                           stage_b_verdict,stage_b_json,stage_b_model,
                           stage_b_cost_usd,stage_b_at,updated_at)
                       VALUES(?,?,?, ?,1,'completed',?,?,?,?,?, ?,?,?,?,?,?,?,?)""",
                    (
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
                        _utc_text(self._now()),
                    ),
                )
                copied += 1
                cursor = await connection.execute(
                    (
                        "UPDATE real_job_status SET status='scored',"
                        "last_status_change_at=? WHERE real_job_id=? AND "
                        "status='new'"
                    ),
                    (_utc_text(self._now()), real_id),
                )
                advanced = cursor.rowcount == 1
                await cursor.close()
                if advanced:
                    await connection.execute(
                        (
                            "INSERT INTO real_job_status_history(real_job_id,"
                            "from_status,to_status,changed_at,reason) "
                            "VALUES(?,'new','scored',?,"
                            "'legacy_evaluation_backfill')"
                        ),
                        (real_id, _utc_text(self._now())),
                    )
        return (int(parents[-1]["id"]) if parents else after_id, copied)


@asynccontextmanager
async def _release_unreturned_claims(
    store: SqliteRealJobEvaluation, claimed: list[RealJobEvaluationInput], stage: str
) -> AsyncIterator[None]:
    """Release committed claims that an interrupted batch cannot hand off."""
    try:
        yield
    except BaseException:
        release = (
            store.release_real_job_stage_a_claim
            if stage == "a"
            else store.release_real_job_stage_b_claim
        )
        for item in claimed:
            await release(
                item.real_job_id,
                expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )
        raise


async def _resolve_sqlite_requirements_hold(
    connection: aiosqlite.Connection, real_id: int, jobs: list[JobPosting]
) -> None:
    """Clear a corrected JD hold only if no other review conflict survives."""
    state = await _one(
        connection,
        "SELECT identity_review_state FROM real_jobs WHERE id=?",
        (real_id,),
    )
    if state is None or state["identity_review_state"] != "requirements_conflict":
        return
    other_review = await _one(
        connection,
        "SELECT 1 FROM real_job_review_cases WHERE "
        "(left_real_job_id=? OR right_real_job_id=?) LIMIT 1",
        (real_id, real_id),
    )
    status_conflict = await _one(
        connection,
        "SELECT COUNT(DISTINCT status) AS n FROM ("
        "SELECT s.status FROM job_status s JOIN jobs j ON j.id=s.job_id "
        "WHERE j.real_job_id=? AND s.status NOT IN ('new','scored') "
        "UNION ALL SELECT status FROM real_job_status WHERE real_job_id=? "
        "AND status NOT IN ('new','scored'))",
        (real_id, real_id),
    )
    evaluated = await _all(
        connection,
        "SELECT e.stage_a_score,e.stage_b_status,e.stage_b_verdict "
        "FROM evaluations e JOIN jobs j ON j.id=e.job_id "
        "WHERE j.real_job_id=? AND e.stage_a_status='completed'",
        (real_id,),
    )
    answers = {
        (row["stage_a_score"], row["stage_b_status"], row["stage_b_verdict"])
        for row in evaluated
    }
    if len(answers) > 1:
        await connection.execute(
            "UPDATE real_jobs SET identity_review_state='evaluation_conflict' "
            "WHERE id=?",
            (real_id,),
        )
    elif (
        other_review is None
        and status_conflict is not None
        and status_conflict["n"] <= 1
        and await _select_sqlite_real_job_input(
            connection, real_id, jobs, now=datetime.now(UTC)
        )
        is not None
    ):
        await connection.execute(
            "UPDATE real_jobs SET identity_review_state='clear' WHERE id=?",
            (real_id,),
        )


async def sync_sqlite_real_job_input(
    connection: aiosqlite.Connection, source_id: int
) -> None:
    """Invalidate a changed canonical input within its source write.

    Args:
        connection: Open source-write transaction.
        source_id: Source posting whose input may have changed.
    """
    connection.row_factory = aiosqlite.Row
    present = await _one(
        connection,
        (
            "SELECT 1 FROM sqlite_schema WHERE type='table' "
            "AND name='real_job_evaluations'"
        ),
        (),
    )
    if present is None:
        return
    parent = await _one(
        connection, "SELECT real_job_id FROM jobs WHERE id=?", (source_id,)
    )
    if parent is None or parent["real_job_id"] is None:
        return
    real_id = int(parent["real_job_id"])
    sources = await _all(
        connection, "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id", (real_id,)
    )
    jobs = [_job_from_row(row) for row in sources]
    representative = representative_source_id(str(real_id), jobs)
    closure = official_closed_at(jobs)
    first_discovered, canonical_posted = canonical_sort_dates(jobs)
    await connection.execute(
        "UPDATE real_jobs SET representative_job_id=?,official_closed_at=?,"
        "first_discovered_at=?,canonical_posted_at=? WHERE id=?",
        (
            representative,
            _utc_text(closure) if closure else None,
            _utc_text(first_discovered),
            _utc_text(canonical_posted),
            real_id,
        ),
    )
    override = await _review_source_override(connection, real_id, jobs)
    if (conflict := conflicting_complete_sources(jobs)) and override is None:
        # Existing source scores need the backfill's more specific evaluation hold.
        old_score = await _one(
            connection,
            "SELECT 1 FROM evaluations e JOIN jobs j ON j.id=e.job_id "
            "WHERE j.real_job_id=? AND e.stage_a_status='completed' LIMIT 1",
            (real_id,),
        )
        if old_score is None:
            await connection.execute(
                "UPDATE real_jobs SET identity_review_state='requirements_conflict' "
                "WHERE id=? AND identity_review_state='clear'",
                (real_id,),
            )
        await connection.execute(
            "INSERT INTO real_job_review_cases("
            "left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason) "
            "SELECT ?,?,?,?,'requirements_conflict' WHERE NOT EXISTS("
            "SELECT 1 FROM real_job_review_cases WHERE left_real_job_id=? "
            "AND right_real_job_id=? AND reason='requirements_conflict')",
            (real_id, real_id, *conflict, real_id, real_id),
        )
    else:
        await connection.execute(
            "DELETE FROM real_job_review_cases WHERE left_real_job_id=? "
            "AND right_real_job_id=? AND reason='requirements_conflict'",
            (real_id, real_id),
        )
        await _resolve_sqlite_requirements_hold(connection, real_id, jobs)
    current = await _one(
        connection, "SELECT * FROM real_job_evaluations WHERE real_job_id=?", (real_id,)
    )
    if current is None:
        await _hold_unbackfilled_source_scores(connection, real_id, jobs)
        return
    selected = await _select_sqlite_real_job_input(
        connection, real_id, jobs, now=datetime.now(UTC)
    )
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
            await connection.execute(
                "UPDATE real_job_evaluations SET source_job_id=?,input_jd_text=? "
                "WHERE real_job_id=?",
                (int(selected.source_job_id), selected.job.jd_text, real_id),
            )
            return
    if current["stage_a_status"] is None and current["stage_b_status"] is None:
        return
    now = _utc_text(datetime.now(UTC))
    await connection.execute(
        """INSERT INTO real_job_evaluation_history(
               real_job_id,source_job_id,input_revision,input_facts_json,stage_a_status,
               stage_a_score,stage_b_status,stage_b_verdict,stage_b_json,
               archived_at,reason)
           VALUES(?,?,?,?,?,?,?,?,?,?,'input_changed')""",
        (
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
        ),
    )
    if selected is None:
        await connection.execute(
            (
                "UPDATE real_jobs SET identity_review_state='requ"
                "irements_conflict' WHERE id=?"
            ),
            (real_id,),
        )
        source_job_id = current["source_job_id"]
        jd_text = current["input_jd_text"]
        facts = current["input_facts_json"]
    else:
        source_job_id = int(selected.source_job_id)
        jd_text = selected.job.jd_text
    await connection.execute(
        """UPDATE real_job_evaluations SET
               source_job_id=?,input_jd_text=?,input_facts_json=?,
               input_revision=input_revision+1,stage_a_status=NULL,
               stage_a_score=NULL,stage_a_one_line=NULL,
               stage_a_timing_eligible=NULL,stage_a_model=NULL,
               stage_a_cost_usd=NULL,stage_a_at=NULL,stage_b_status=NULL,
               stage_b_verdict=NULL,stage_b_json=NULL,stage_b_model=NULL,
               stage_b_cost_usd=NULL,stage_b_at=NULL,ml_gate_result=NULL,
               ml_gate_score=NULL,ml_gate_json=NULL,updated_at=?
           WHERE real_job_id=?""",
        (source_job_id, jd_text, facts, now, real_id),
    )


async def _hold_unbackfilled_source_scores(
    connection: aiosqlite.Connection, real_id: int, jobs: list[JobPosting]
) -> None:
    """Keep activated parents with old source scores out of canonical claims."""
    activation = await _one(
        connection, "SELECT value FROM state WHERE key=?", (EVALUATION_ACTIVATION_KEY,)
    )
    if activation is None or activation["value"] != "enabled":
        return
    state = await _one(
        connection, "SELECT identity_review_state FROM real_jobs WHERE id=?", (real_id,)
    )
    if state is None or state["identity_review_state"] != "clear":
        return
    if await _review_source_override(connection, real_id, jobs) is not None:
        return
    evaluations = await _all(
        connection,
        "SELECT e.* FROM evaluations e JOIN jobs j ON j.id=e.job_id "
        "WHERE j.real_job_id=? AND e.stage_a_status='completed' "
        "ORDER BY e.job_id",
        (real_id,),
    )
    if not evaluations:
        return
    variants = {
        (
            row["stage_a_score"],
            row["stage_a_one_line"],
            row["stage_a_timing_eligible"],
            row["stage_a_model"],
            row["stage_b_status"],
            _legacy_stage_b_json(row),
        )
        for row in evaluations
    }
    if len(variants) > 1:
        hold = "evaluation_conflict"
    else:
        selected = await _select_sqlite_real_job_input(
            connection, real_id, jobs, now=datetime.now(UTC)
        )
        hold = (
            legacy_evaluation_input_hold(
                selected,
                jobs,
                [
                    (int(row["job_id"]), _datetime_from_text(row["stage_a_at"]))
                    for row in evaluations
                ],
            )
            or "evaluation_input_conflict"
        )
    await connection.execute(
        "UPDATE real_jobs SET identity_review_state=? WHERE id=? "
        "AND identity_review_state='clear'",
        (hold, real_id),
    )


async def _select_sqlite_real_job_input(
    connection: aiosqlite.Connection,
    real_id: int,
    jobs: list[JobPosting],
    *,
    now: datetime,
) -> RealJobEvaluationInput | None:
    """Use an explicitly reviewed source before the strict automatic choice."""
    override = await _review_source_override(connection, real_id, jobs)
    if override is None:
        return select_real_job_input(str(real_id), jobs, now=now)
    first_discovery = min(job.discovered_at for job in jobs)
    original_dates = [
        job.posted_at for job in jobs if job.posted_at is not None and not job.is_repost
    ]
    canonical = replace(
        override,
        discovered_at=first_discovery,
        posted_at=min(original_dates) if original_dates else override.posted_at,
        closed_at=official_closed_at(jobs),
        is_repost=all(job.is_repost is True for job in jobs),
    )
    assert override.id is not None
    return RealJobEvaluationInput(str(real_id), override.id, canonical)


async def _review_source_override(
    connection: aiosqlite.Connection,
    real_id: int,
    jobs: list[JobPosting],
) -> JobPosting | None:
    marker = await _one(
        connection,
        "SELECT value FROM state WHERE key=?",
        (f"{_REVIEW_SOURCE_KEY}{real_id}",),
    )
    if marker is None:
        return None
    source_id = int(marker["value"])
    return next(
        (
            job
            for job in jobs
            if job.id is not None
            and int(job.id) == source_id
            and job.jd_text
            and job.jd_quality in _COMPLETE_QUALITY
        ),
        None,
    )


async def _one(
    connection: aiosqlite.Connection, sql: str, params: tuple[object, ...]
) -> aiosqlite.Row | None:
    cursor = await connection.execute(sql, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def _all(
    connection: aiosqlite.Connection, sql: str, params: tuple[object, ...]
) -> list[aiosqlite.Row]:
    cursor = await connection.execute(sql, params)
    try:
        return list(await cursor.fetchall())
    finally:
        await cursor.close()


def _too_old(item: RealJobEvaluationInput, now: datetime, max_days: int | None) -> bool:
    if max_days is None:
        return False
    effective = item.job.posted_at
    if effective is None or effective > now:
        effective = item.job.discovered_at
    return effective < now - timedelta(days=max_days)


def _legacy_stage_b_json(row: aiosqlite.Row) -> str | None:
    if row["stage_b_status"] != "completed":
        return None
    fit = json.loads(row["stage_b_fit_json"] or "{}")
    return _canonical_json(
        {
            "verdict": row["stage_b_verdict"],
            "jd_summary": row["stage_b_jd_summary"],
            "fit_analysis": {"score": fit.get("score_0_100"), **fit},
            "resume_hooks": json.loads(row["stage_b_hooks_json"] or "{}"),
            "raw_blocks": {
                "verdict": json.loads(row["stage_b_verdict_json"] or "{}"),
                "jd_summary": json.loads(row["stage_b_summary_json"] or "{}"),
                "fit_analysis": fit,
            },
        }
    )


def _required_updated_at(value: str) -> datetime:
    """Read the non-null claim timestamp enforced by the evaluation schema."""
    timestamp = _datetime_from_text(value)
    assert timestamp is not None
    return timestamp
