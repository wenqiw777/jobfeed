"""PostgreSQL golden contracts for evaluation errors and claim release."""

from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting, QualityBand, StageAResult
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

EXPECTED_ERROR_COUNT = 2


def _job(canonical_id: str) -> JobPosting:
    return make_job(
        canonical_id,
        company=f"Company {canonical_id}",
        jd_text="Detailed JD",
        jd_quality=QualityBand.GOOD,
    )


def _stage_a() -> StageAResult:
    return StageAResult(
        score=80,
        one_line="Fit",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="stage-a-prompt",
        resume_hash="resume-a",
        cost_usd=0.01,
    )


async def _evaluation_rows(
    store: PostgresStore,
    job_ids: list[str],
) -> dict[str, dict[str, Any]]:
    pool = store._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT job_id,
                      stage_a_status, stage_a_error, stage_a_score,
                      stage_b_status, stage_b_error, stage_b_verdict,
                      stage_b_error_count, updated_at
                 FROM evaluations
                WHERE job_id = ANY($1::bigint[])""",
            [int(job_id) for job_id in job_ids],
        )
    return {str(row["job_id"]): dict(row) for row in rows}


async def _set_stage_b_in_progress(
    store: PostgresStore,
    job_ids: list[str],
) -> None:
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE evaluations
                  SET stage_b_status = 'in_progress', updated_at = now()
                WHERE job_id = ANY($1::bigint[])""",
            [int(job_id) for job_id in job_ids],
        )


async def test_save_stage_b_error_counts_retries_and_preserves_latest_error(
    store: PostgresStore,
) -> None:
    """Every Stage B failure increments once and records the newest detail."""
    saved = await store.save_job(_job("stage-b-errors"))

    await store.save_stage_b_error(saved.job_id, "first")
    await store.save_stage_b_error(saved.job_id, "second")

    row = (await _evaluation_rows(store, [saved.job_id]))[saved.job_id]
    assert row["stage_b_status"] == "error"
    assert row["stage_b_error"] == "second"
    assert row["stage_b_error_count"] == EXPECTED_ERROR_COUNT
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await store.save_stage_b_error("999999999", "missing")


async def test_release_stage_a_claim_restores_all_prior_states_idempotently(
    store: PostgresStore,
) -> None:
    """Stage A release restores error, completed, or pending from row evidence."""
    error_job = await store.save_job(_job("release-a-error"))
    completed_job = await store.save_job(_job("release-a-completed"))
    pending_job = await store.save_job(_job("release-a-pending"))
    await store.save_stage_a_error(error_job.job_id, "retry")
    await store.save_stage_a(completed_job.job_id, _stage_a())
    job_ids = [error_job.job_id, completed_job.job_id, pending_job.job_id]
    claimed = await store.claim_stage_a_by_ids(job_ids, corpus="all")
    assert {job.id for job in claimed} == set(job_ids)

    for job_id in job_ids:
        await store.release_stage_a_claim(job_id)

    released = await _evaluation_rows(store, job_ids)
    assert released[error_job.job_id]["stage_a_status"] == "error"
    assert released[completed_job.job_id]["stage_a_status"] == "completed"
    assert released[pending_job.job_id]["stage_a_status"] is None
    timestamps = {job_id: released[job_id]["updated_at"] for job_id in job_ids}
    for job_id in job_ids:
        await store.release_stage_a_claim(job_id)
    await store.release_stage_a_claim("999999999")
    replayed = await _evaluation_rows(store, job_ids)
    assert {job_id: replayed[job_id]["updated_at"] for job_id in job_ids} == timestamps


async def test_release_stage_b_claim_restores_all_prior_states_idempotently(
    store: PostgresStore,
) -> None:
    """Stage B release restores error, completed, or pending from row evidence."""
    error_job = await store.save_job(_job("release-b-error"))
    completed_job = await store.save_job(_job("release-b-completed"))
    pending_job = await store.save_job(_job("release-b-pending"))
    job_ids = [error_job.job_id, completed_job.job_id, pending_job.job_id]
    for job_id in job_ids:
        await store.save_stage_a(job_id, _stage_a())
    await store.save_stage_b_error(error_job.job_id, "retry")
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE evaluations
                  SET stage_b_verdict = 'consider'
                WHERE job_id = $1""",
            int(completed_job.job_id),
        )
    await _set_stage_b_in_progress(store, job_ids)

    for job_id in job_ids:
        await store.release_stage_b_claim(job_id)

    released = await _evaluation_rows(store, job_ids)
    assert released[error_job.job_id]["stage_b_status"] == "error"
    assert released[completed_job.job_id]["stage_b_status"] == "completed"
    assert released[pending_job.job_id]["stage_b_status"] is None
    timestamps = {job_id: released[job_id]["updated_at"] for job_id in job_ids}
    for job_id in job_ids:
        await store.release_stage_b_claim(job_id)
    await store.release_stage_b_claim("999999999")
    replayed = await _evaluation_rows(store, job_ids)
    assert {job_id: replayed[job_id]["updated_at"] for job_id in job_ids} == timestamps
