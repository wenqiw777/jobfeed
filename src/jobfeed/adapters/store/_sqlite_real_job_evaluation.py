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
from jobfeed.domain.intermediary import intermediary_posting
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
_MAX_CLAIM_PAGE = 100
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

        Time complexity: O(n * s**2) for n parents with at most s sources;
        source equivalence checks stay within pages of at most 100 parents.

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
        async with (
            _release_unreturned_claims(self, claimed, "a"),
            self._lifecycle.connection() as connection,
        ):
            connection.row_factory = aiosqlite.Row
            for offset in range(0, len(ids), _MAX_CLAIM_PAGE):
                if len(claimed) >= limit:
                    break
                batch = ids[offset : offset + _MAX_CLAIM_PAGE]
                async with _immediate_transaction(connection):
                    parents, evaluations, sources, overrides = await _claim_inputs(
                        connection, batch
                    )
                    for real_id in batch:
                        if len(claimed) >= limit:
                            break
                        state = parents.get(real_id)
                        if state is None or state["identity_review_state"] != "clear":
                            continue
                        selected = _select_input_with_override(
                            real_id,
                            sources.get(real_id, []),
                            overrides.get(real_id),
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
                        current = evaluations.get(real_id)
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

        Time complexity: O(n * s**2) for n parents with at most s sources;
        each writer transaction is bounded to at most 100 parents.

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
        ids = list(dict.fromkeys(int(value) for value in real_job_ids))
        async with (
            _release_unreturned_claims(self, claimed, "b"),
            self._lifecycle.connection() as connection,
        ):
            connection.row_factory = aiosqlite.Row
            for offset in range(0, len(ids), _MAX_CLAIM_PAGE):
                if len(claimed) >= limit:
                    break
                batch = ids[offset : offset + _MAX_CLAIM_PAGE]
                async with _immediate_transaction(connection):
                    parents, evaluations, sources, overrides = await _claim_inputs(
                        connection, batch
                    )
                    for real_id in batch:
                        if len(claimed) >= limit:
                            break
                        state = parents.get(real_id)
                        if state is None or state["identity_review_state"] != "clear":
                            continue
                        current = evaluations.get(real_id)
                        if (
                            current is None
                            or current["stage_a_status"] != "completed"
                            or current["stage_a_score"] < stage_a_threshold
                        ):
                            continue
                        selected = _select_input_with_override(
                            real_id,
                            sources.get(real_id, []),
                            overrides.get(real_id),
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
                                "stage_b_status='in_progress',input_facts_json=?,"
                                "claim_generation=claim_generation+1,"
                                "updated_at=? WHERE "
                                "real_job_id=?"
                            ),
                            (
                                replace_evaluation_policies(
                                    current["input_facts_json"],
                                    stage_b_policy=stage_b_policy,
                                ),
                                timestamp,
                                real_id,
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
                if await _adopt_sqlite_legacy_score(
                    connection,
                    real_id,
                    [_job_from_row(row) for row in sources],
                    self._now(),
                ):
                    copied += 1
        return (int(parents[-1]["id"]) if parents else after_id, copied)


async def _adopt_sqlite_legacy_score(
    connection: aiosqlite.Connection,
    real_id: int,
    jobs: list[JobPosting],
    now: datetime,
) -> bool:
    """Adopt one whole historical answer, recording its original source."""
    evaluations = await _all(
        connection,
        "SELECT e.* FROM evaluations e JOIN jobs j ON j.id=e.job_id "
        "WHERE j.real_job_id=? AND e.stage_a_status='completed' "
        "ORDER BY e.job_id",
        (real_id,),
    )
    if not evaluations:
        return False
    selected = await _select_sqlite_real_job_input(connection, real_id, jobs, now=now)
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
        return False
    assert selected is not None
    chosen = max(
        evaluations,
        key=lambda row: (
            row["stage_a_score"] if row["stage_a_score"] is not None else -1,
            _datetime_from_text(row["stage_a_at"]) or datetime.min.replace(tzinfo=UTC),
            int(row["job_id"]),
        ),
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
            _utc_text(now),
        ),
    )
    cursor = await connection.execute(
        (
            "UPDATE real_job_status SET status='scored',"
            "last_status_change_at=? WHERE real_job_id=? AND "
            "status='new'"
        ),
        (_utc_text(now), real_id),
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
            (real_id, _utc_text(now)),
        )
    await connection.execute(
        "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO "
        "UPDATE SET value=excluded.value",
        (
            f"real-job-legacy-adoption:{real_id}:1",
            json.dumps(
                {
                    "reason": "user_approved_legacy_score_reuse",
                    "input_revision": 1,
                    "score_source_job_id": int(chosen["job_id"]),
                    "input_source_job_id": int(selected.source_job_id),
                    "stage_a_score": chosen["stage_a_score"],
                    "adopted_at": _utc_text(now),
                },
                sort_keys=True,
            ),
        ),
    )
    return True


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
    if (
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
    if current["stage_a_status"] == "completed" or (
        current["stage_a_status"] is None and current["stage_b_status"] is None
    ):
        # Keep the paid result and its original input as an audit snapshot.
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
    await _adopt_sqlite_legacy_score(connection, real_id, jobs, datetime.now(UTC))


async def _select_sqlite_real_job_input(
    connection: aiosqlite.Connection,
    real_id: int,
    jobs: list[JobPosting],
    *,
    now: datetime,
) -> RealJobEvaluationInput | None:
    """Use an explicitly reviewed source before the strict automatic choice."""
    override = await _review_source_override(connection, real_id, jobs)
    return _select_input_with_override(real_id, jobs, override, now=now)


def _select_input_with_override(
    real_id: int,
    jobs: list[JobPosting],
    override: JobPosting | None,
    *,
    now: datetime,
) -> RealJobEvaluationInput | None:
    """Select from a transaction-consistent source and manual-choice snapshot."""
    if override is not None and intermediary_posting(override):
        return None
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


async def reconcile_legacy_review_page(
    connection: aiosqlite.Connection,
    *,
    after_id: int = 0,
    limit: int = 100,
    apply: bool = False,
) -> list[dict[str, object]]:
    """Preview or apply the approved policy to existing holds in a bounded page.

    Source postings and source evaluations are never edited. Callers applying
    this migration must stop writers and retain a backup; read-only callers may
    use a SQLite mode=ro connection. Missing descriptions remain held.

    Args:
        connection: Existing SQLite connection; read-only is valid for preview.
        after_id: Exclusive canonical ID cursor for the next page.
        limit: Maximum held parents to inspect, from 1 through 1000.
        apply: Persist approved decisions when true; otherwise only inspect.

    Returns:
        Per-parent actions, retained-hold reasons and legacy score provenance.

    Raises:
        ValueError: If limit is outside the supported range.
    """
    if not 1 <= limit <= _MAX_BACKFILL_PAGE:
        raise ValueError("limit must be between 1 and 1000")
    connection.row_factory = aiosqlite.Row
    parents = await _all(
        connection,
        "SELECT id FROM real_jobs WHERE id>? AND identity_review_state IN "
        "('evaluation_conflict','evaluation_input_conflict','requirements_conflict',"
        "'evaluation_input_missing') ORDER BY id LIMIT ?",
        (after_id, limit),
    )
    report = []
    for parent in parents:
        if apply:
            async with _immediate_transaction(connection):
                report.append(
                    await _reconcile_legacy_review(
                        connection, int(parent["id"]), apply=True
                    )
                )
        else:
            report.append(
                await _reconcile_legacy_review(
                    connection, int(parent["id"]), apply=False
                )
            )
    return report


async def _reconcile_legacy_review(
    connection: aiosqlite.Connection, real_id: int, *, apply: bool
) -> dict[str, object]:
    parent = await _one(
        connection, "SELECT identity_review_state FROM real_jobs WHERE id=?", (real_id,)
    )
    assert parent is not None
    report: dict[str, object] = {
        "real_job_id": real_id,
        "previous_state": parent["identity_review_state"],
        "action": "keep_hold",
    }
    if parent["identity_review_state"] == "evaluation_input_missing":
        report["reason"] = "missing_input_requires_separate_decision"
        return report
    rows = await _all(
        connection, "SELECT * FROM jobs WHERE real_job_id=? ORDER BY id", (real_id,)
    )
    jobs = [_job_from_row(row) for row in rows]
    selected = await _select_sqlite_real_job_input(
        connection, real_id, jobs, now=datetime.now(UTC)
    )
    if selected is None:
        report["reason"] = "no_unambiguous_complete_input"
        return report
    other = await _one(
        connection,
        "SELECT 1 FROM real_job_review_cases WHERE "
        "(left_real_job_id=? OR right_real_job_id=?) "
        "AND reason!='requirements_conflict' LIMIT 1",
        (real_id, real_id),
    )
    statuses = await _one(
        connection,
        "SELECT COUNT(DISTINCT status) AS n FROM (SELECT s.status "
        "FROM job_status s JOIN jobs j "
        "ON j.id=s.job_id WHERE j.real_job_id=? AND s.status NOT IN "
        "('new','scored') UNION ALL "
        "SELECT status FROM real_job_status WHERE real_job_id=? AND "
        "status NOT IN ('new','scored'))",
        (real_id, real_id),
    )
    if other is not None or (statuses is not None and statuses["n"] > 1):
        report["reason"] = "other_identity_or_workflow_conflict"
        return report
    current = await _one(
        connection, "SELECT * FROM real_job_evaluations WHERE real_job_id=?", (real_id,)
    )
    if current is not None and "in_progress" in (
        current["stage_a_status"],
        current["stage_b_status"],
    ):
        report["reason"] = "active_canonical_claim"
        return report
    evaluations = await _all(
        connection,
        "SELECT e.* FROM evaluations e JOIN jobs j ON j.id=e.job_id "
        "WHERE j.real_job_id=? "
        "AND e.stage_a_status='completed'",
        (real_id,),
    )
    if current is None and evaluations:
        hold = legacy_evaluation_input_hold(
            selected,
            jobs,
            [
                (int(row["job_id"]), _datetime_from_text(row["stage_a_at"]))
                for row in evaluations
            ],
        )
        if hold is not None:
            report["reason"] = hold
            return report
        chosen = max(
            evaluations,
            key=lambda row: (
                row["stage_a_score"] if row["stage_a_score"] is not None else -1,
                _datetime_from_text(row["stage_a_at"])
                or datetime.min.replace(tzinfo=UTC),
                int(row["job_id"]),
            ),
        )
        report.update(
            action="adopt_legacy",
            score_source_job_id=int(chosen["job_id"]),
            stage_a_score=chosen["stage_a_score"],
        )
    else:
        report["action"] = "clear_hold_pending"
        if current is not None:
            unchanged = same_evaluation_input(
                current["input_jd_text"], current["input_facts_json"], selected
            )
            report["action"] = (
                "clear_hold_keep_canonical"
                if unchanged or current["stage_a_status"] == "completed"
                else "clear_hold_invalidate_changed_input"
            )
            report["previous_input_revision"] = current["input_revision"]
            report["previous_stage_a_score"] = current["stage_a_score"]
    report["input_source_job_id"] = int(selected.source_job_id)
    if apply:
        await connection.execute(
            "UPDATE real_jobs SET identity_review_state='clear' WHERE id=?", (real_id,)
        )
        await connection.execute(
            "DELETE FROM real_job_review_cases WHERE left_real_job_id=? "
            "AND right_real_job_id=? AND reason='requirements_conflict'",
            (real_id, real_id),
        )
        if current is None and evaluations:
            await _adopt_sqlite_legacy_score(
                connection, real_id, jobs, datetime.now(UTC)
            )
        else:
            await sync_sqlite_real_job_input(connection, int(selected.source_job_id))
    return report


async def _claim_inputs(
    connection: aiosqlite.Connection, ids: list[int]
) -> tuple[
    dict[int, aiosqlite.Row],
    dict[int, aiosqlite.Row],
    dict[int, list[JobPosting]],
    dict[int, JobPosting],
]:
    """Read one bounded claim page inside the caller's writer transaction."""
    placeholders = ",".join("?" for _ in ids)
    parents = await _all(
        connection,
        f"SELECT id,identity_review_state FROM real_jobs WHERE id IN ({placeholders})",
        tuple(ids),
    )
    evaluations = await _all(
        connection,
        f"SELECT * FROM real_job_evaluations WHERE real_job_id IN ({placeholders})",
        tuple(ids),
    )
    rows = await _all(
        connection,
        f"SELECT * FROM jobs WHERE real_job_id IN ({placeholders}) ORDER BY id",
        tuple(ids),
    )
    markers = await _all(
        connection,
        f"SELECT key,value FROM state WHERE key IN ({placeholders})",
        tuple(f"{_REVIEW_SOURCE_KEY}{real_id}" for real_id in ids),
    )
    override_ids = {
        int(row["key"].removeprefix(_REVIEW_SOURCE_KEY)): int(row["value"])
        for row in markers
    }
    sources: dict[int, list[JobPosting]] = {}
    overrides: dict[int, JobPosting] = {}
    for row in rows:
        real_id = int(row["real_job_id"])
        job = _job_from_row(row)
        sources.setdefault(real_id, []).append(job)
        if (
            override_ids.get(real_id) == int(row["id"])
            and job.jd_text
            and job.jd_quality in _COMPLETE_QUALITY
        ):
            overrides[real_id] = job
    return (
        {int(row["id"]): row for row in parents},
        {int(row["real_job_id"]): row for row in evaluations},
        sources,
        overrides,
    )
