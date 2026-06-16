"""save_stage_a status-advance tests against PostgreSQL.

Pins the atomic ``new -> scored`` advance written with the Stage A upsert:
fires only from ``new`` with a single ``auto_scored`` history row, is
idempotent on re-evaluation, leaves workflow-progressed jobs untouched, and
never fires on the error path (``save_stage_a_error``).
"""

from __future__ import annotations

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import StageAResult, TransitionRequest
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

_STAGE_A_SCORE = 80


def _stage_a(score: int = _STAGE_A_SCORE) -> StageAResult:
    """Build a minimal completed Stage A result.

    Args:
        score: Desired Stage A score.

    Returns:
        StageAResult fixture.
    """
    return StageAResult(
        score=score,
        one_line="fits",
        timing_eligible="yes",
        model="mock/stage-a",
        prompt_hash="prompt-a",
        resume_hash="resume-a",
    )


async def _current_status(store: PostgresStore, job_id: str) -> str:
    """Read the job's current status straight from job_status.

    Args:
        store: Connected store.
        job_id: Store-assigned identity.

    Returns:
        Current status string.
    """
    pool = store._get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM job_status WHERE job_id = $1",
            int(job_id),
        )


async def _scored_history_rows(store: PostgresStore, job_id: str) -> list:
    """Fetch all history rows that transition the job to scored.

    Args:
        store: Connected store.
        job_id: Store-assigned identity.

    Returns:
        List of (from_status, to_status, reason) records.
    """
    pool = store._get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT from_status, to_status, reason
               FROM job_status_history
               WHERE job_id = $1 AND to_status = 'scored'""",
            int(job_id),
        )


async def test_save_stage_a_advances_new_to_scored_with_history(
    store: PostgresStore,
) -> None:
    """Stage A on a new job lands status=scored plus one auto_scored row."""
    saved = await store.save_job(make_job("sa-status-new"))

    await store.save_stage_a(saved.job_id, _stage_a())

    assert await _current_status(store, saved.job_id) == "scored"
    rows = await _scored_history_rows(store, saved.job_id)
    assert len(rows) == 1
    assert rows[0]["from_status"] == "new"
    assert rows[0]["reason"] == "auto_scored"


async def test_save_stage_a_leaves_progressed_status_untouched(
    store: PostgresStore,
) -> None:
    """Stage A on a shortlisted job neither moves status nor writes history."""
    saved = await store.save_job(make_job("sa-status-shortlisted"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="shortlisted", force=True)
    )

    await store.save_stage_a(saved.job_id, _stage_a())

    assert await _current_status(store, saved.job_id) == "shortlisted"
    assert await _scored_history_rows(store, saved.job_id) == []


async def test_save_stage_a_reeval_is_idempotent(store: PostgresStore) -> None:
    """A second Stage A write keeps scored and adds no extra history row."""
    saved = await store.save_job(make_job("sa-status-reeval"))

    await store.save_stage_a(saved.job_id, _stage_a())
    await store.save_stage_a(saved.job_id, _stage_a(score=90))

    assert await _current_status(store, saved.job_id) == "scored"
    rows = await _scored_history_rows(store, saved.job_id)
    assert len(rows) == 1


async def test_save_stage_a_error_keeps_status_new(store: PostgresStore) -> None:
    """The Stage A error path records the error but never advances status."""
    saved = await store.save_job(make_job("sa-status-error"))

    await store.save_stage_a_error(saved.job_id, "llm timeout")

    assert await _current_status(store, saved.job_id) == "new"
    assert await _scored_history_rows(store, saved.job_id) == []
