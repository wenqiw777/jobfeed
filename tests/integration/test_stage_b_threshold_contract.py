"""PostgreSQL golden contracts for Stage B batch and threshold operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting, QualityBand, StageAResult
from jobfeed.domain.scoring import MAX_STAGE_RETRIES
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

STAGE_B_THRESHOLD = 70
EXPECTED_THRESHOLD_CHANGES = 2


def _job(canonical_id: str, *, discovered_at: datetime | None = None) -> JobPosting:
    """Build a unique, Stage B eligible job fixture."""
    return make_job(
        canonical_id,
        company=f"Company {canonical_id}",
        jd_text="Detailed JD",
        jd_quality=QualityBand.GOOD,
        discovered_at=discovered_at or datetime.now(UTC),
    )


def _stage_a(score: int) -> StageAResult:
    """Build a completed Stage A result with the requested score."""
    return StageAResult(
        score=score,
        one_line="Fit",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="stage-a-prompt",
        resume_hash="resume-a",
        cost_usd=0.01,
    )


async def _save_scored_job(
    store: PostgresStore,
    canonical_id: str,
    score: int,
    *,
    discovered_at: datetime | None = None,
) -> str:
    """Persist a job with a completed Stage A evaluation and return its ID."""
    saved = await store.save_job(_job(canonical_id, discovered_at=discovered_at))
    await store.save_stage_a(saved.job_id, _stage_a(score))
    return saved.job_id


async def _set_stage_b_state(
    store: PostgresStore,
    job_id: str,
    *,
    status: str,
    age: timedelta = timedelta(0),
    error_count: int = 0,
) -> None:
    """Set a precise Stage B state used to exercise queue boundaries."""
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE evaluations
                  SET stage_b_status = $2,
                      stage_b_error = CASE WHEN $2 = 'error' THEN 'boom' ELSE NULL END,
                      stage_b_error_count = $3,
                      stage_b_verdict = CASE
                          WHEN $2 = 'completed' THEN 'consider'
                          ELSE NULL
                      END,
                      updated_at = now() - $4::interval
                WHERE job_id = $1""",
            int(job_id),
            status,
            error_count,
            age,
        )


async def _stage_b_statuses(
    store: PostgresStore, job_ids: list[str]
) -> dict[str, str | None]:
    """Read Stage B statuses for the requested evaluation rows."""
    pool = store._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT job_id, stage_b_status
                 FROM evaluations
                WHERE job_id = ANY($1::bigint[])""",
            [int(job_id) for job_id in job_ids],
        )
    return {str(row["job_id"]): row["stage_b_status"] for row in rows}


async def test_mark_stage_b_skipped_batch_is_atomic_and_preserves_completed(
    store: PostgresStore,
) -> None:
    """Batch skip validates all IDs first and never overwrites completion."""
    pending_id = await _save_scored_job(store, "batch-pending", 80)
    completed_id = await _save_scored_job(store, "batch-completed", 80)
    await _set_stage_b_state(
        store,
        completed_id,
        status="completed",
    )
    no_evaluation = await store.save_job(_job("batch-no-evaluation"))

    await store.mark_stage_b_skipped_batch([])
    with pytest.raises(ValueError):
        await store.mark_stage_b_skipped_batch([pending_id, "not-an-id"])
    assert (await _stage_b_statuses(store, [pending_id]))[pending_id] is None

    await store.mark_stage_b_skipped_batch(
        [pending_id, completed_id, no_evaluation.job_id, "999999999"]
    )

    statuses = await _stage_b_statuses(
        store,
        [pending_id, completed_id, no_evaluation.job_id],
    )
    assert statuses == {
        pending_id: "skipped_below_threshold",
        completed_id: "completed",
    }


async def test_mark_stage_b_below_threshold_honors_claim_and_freshness_guards(
    store: PostgresStore,
) -> None:
    """Threshold sync skips only retryable, fresh-corpus, unowned low scores."""
    now = datetime.now(UTC)
    pending_id = await _save_scored_job(store, "low-pending", 40)
    stale_id = await _save_scored_job(store, "low-stale-claim", 40)
    fresh_id = await _save_scored_job(store, "low-fresh-claim", 40)
    over_cap_id = await _save_scored_job(store, "low-over-cap", 40)
    completed_id = await _save_scored_job(store, "low-completed", 40)
    old_id = await _save_scored_job(
        store,
        "low-old",
        40,
        discovered_at=now - timedelta(days=30),
    )
    await _set_stage_b_state(
        store,
        stale_id,
        status="in_progress",
        age=timedelta(hours=2),
    )
    await _set_stage_b_state(
        store,
        fresh_id,
        status="in_progress",
        age=timedelta(minutes=5),
    )
    await _set_stage_b_state(
        store,
        over_cap_id,
        status="error",
        error_count=MAX_STAGE_RETRIES,
    )
    await _set_stage_b_state(
        store,
        completed_id,
        status="completed",
    )

    changed = await store.mark_stage_b_below_threshold(
        STAGE_B_THRESHOLD,
        max_days=7,
    )

    assert changed == EXPECTED_THRESHOLD_CHANGES
    assert await _stage_b_statuses(
        store,
        [pending_id, stale_id, fresh_id, over_cap_id, completed_id, old_id],
    ) == {
        pending_id: "skipped_below_threshold",
        stale_id: "skipped_below_threshold",
        fresh_id: "in_progress",
        over_cap_id: "error",
        completed_id: "completed",
        old_id: None,
    }


async def test_stage_b_preview_matches_immediate_threshold_sync_and_claim(
    store: PostgresStore,
) -> None:
    """Read-only preview equals the actual uncontended sync-and-claim result."""
    discovered_at = datetime.now(UTC)
    pending_id = await _save_scored_job(
        store, "preview-pending", 80, discovered_at=discovered_at
    )
    skipped_id = await _save_scored_job(
        store, "preview-skipped", 80, discovered_at=discovered_at
    )
    error_id = await _save_scored_job(
        store, "preview-error", 80, discovered_at=discovered_at
    )
    stale_id = await _save_scored_job(
        store, "preview-stale", 80, discovered_at=discovered_at
    )
    fresh_id = await _save_scored_job(
        store, "preview-fresh", 80, discovered_at=discovered_at
    )
    below_id = await _save_scored_job(
        store, "preview-below", 40, discovered_at=discovered_at
    )
    completed_id = await _save_scored_job(
        store, "preview-completed", 80, discovered_at=discovered_at
    )
    over_cap_id = await _save_scored_job(
        store, "preview-over-cap", 80, discovered_at=discovered_at
    )
    await store.mark_stage_b_skipped(skipped_id)
    await _set_stage_b_state(store, error_id, status="error", error_count=1)
    await _set_stage_b_state(
        store,
        stale_id,
        status="in_progress",
        age=timedelta(hours=2),
    )
    await _set_stage_b_state(
        store,
        fresh_id,
        status="in_progress",
        age=timedelta(minutes=5),
    )
    await _set_stage_b_state(
        store,
        completed_id,
        status="completed",
    )
    await _set_stage_b_state(
        store,
        over_cap_id,
        status="error",
        error_count=MAX_STAGE_RETRIES,
    )
    all_ids = [
        pending_id,
        skipped_id,
        error_id,
        stale_id,
        fresh_id,
        below_id,
        completed_id,
        over_cap_id,
    ]
    before = await _stage_b_statuses(store, all_ids)

    preview = await store.preview_pending_stage_b_after_threshold_sync(
        stage_a_threshold=STAGE_B_THRESHOLD,
    )

    assert await _stage_b_statuses(store, all_ids) == before
    assert await store.reopen_stage_b_at_or_above_threshold(STAGE_B_THRESHOLD) == 1
    assert await store.mark_stage_b_below_threshold(STAGE_B_THRESHOLD) == 1
    claimed = await store.claim_pending_stage_b(
        stage_a_threshold=STAGE_B_THRESHOLD,
    )
    preview_ids = [job.canonical_id for job in preview]
    claimed_ids = [job.canonical_id for job in claimed]
    assert preview_ids == claimed_ids
    assert claimed_ids == [
        "preview-stale",
        "preview-error",
        "preview-skipped",
        "preview-pending",
    ]
