"""Independent-process concurrency contracts for PostgreSQL store operations."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting, QualityBand, StageAResult
from tests.support.factories import make_job
from tests.support.pg_process_race import (
    run_store_process_race,
    wait_for_process_signal,
)

pytestmark = pytest.mark.postgres

WORKER_COUNT = 2
CLAIM_JOB_COUNT = 8
READER_TIMEOUT_SECONDS = 2.0


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


def _claimed_ids(result: dict[str, object]) -> set[str]:
    value = result["job_ids"]
    assert isinstance(value, list)
    return {str(job_id) for job_id in value}


async def _set_claim_age(
    store: PostgresStore,
    job_id: str,
    *,
    stage: str,
    age: timedelta,
) -> None:
    if stage not in {"a", "b"}:
        raise ValueError("stage must be 'a' or 'b'")
    status_column = f"stage_{stage}_status"
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""UPDATE evaluations
                   SET {status_column} = 'in_progress',
                       updated_at = now() - $2::interval
                 WHERE job_id = $1""",
            int(job_id),
            age,
        )


async def test_two_processes_save_same_natural_key_truthfully(
    store: PostgresStore,
    migrated_pg_url: str,
    tmp_path: Path,
) -> None:
    """Exactly one process inserts; the other updates the same stored row."""
    payload = {
        "operation": "save_job",
        "canonical_id": "same-natural-key",
        "company": "Same Company",
    }

    results = await run_store_process_race(
        dsn=migrated_pg_url,
        payloads=[payload.copy() for _ in range(WORKER_COUNT)],
        sync_dir=tmp_path / "save-job-race",
    )

    assert sum(result["inserted"] is True for result in results) == 1
    assert sum(result["updated"] is True for result in results) == 1
    assert len({result["job_id"] for result in results}) == 1
    assert await store.count_rows("jobs") == 1


async def test_two_processes_never_duplicate_stage_a_paid_claims(
    store: PostgresStore,
    migrated_pg_url: str,
    tmp_path: Path,
) -> None:
    """Two independent scorers claim a disjoint partition of Stage A jobs."""
    expected_ids = set()
    for index in range(CLAIM_JOB_COUNT):
        saved = await store.save_job(_job(f"stage-a-race-{index}"))
        expected_ids.add(saved.job_id)
    payload = {
        "operation": "claim_stage_a",
        "corpus": "all",
        "limit": CLAIM_JOB_COUNT,
    }

    results = await run_store_process_race(
        dsn=migrated_pg_url,
        payloads=[payload.copy() for _ in range(WORKER_COUNT)],
        sync_dir=tmp_path / "stage-a-race",
    )

    claimed_sets = [_claimed_ids(result) for result in results]
    assert claimed_sets[0].isdisjoint(claimed_sets[1])
    assert claimed_sets[0] | claimed_sets[1] == expected_ids


async def test_two_processes_never_duplicate_stage_b_paid_claims(
    store: PostgresStore,
    migrated_pg_url: str,
    tmp_path: Path,
) -> None:
    """Two independent LLM workers claim a disjoint partition of Stage B jobs."""
    expected_ids = set()
    for index in range(CLAIM_JOB_COUNT):
        saved = await store.save_job(_job(f"stage-b-race-{index}"))
        await store.save_stage_a(saved.job_id, _stage_a())
        expected_ids.add(saved.job_id)
    payload = {"operation": "claim_stage_b", "limit": CLAIM_JOB_COUNT}

    results = await run_store_process_race(
        dsn=migrated_pg_url,
        payloads=[payload.copy() for _ in range(WORKER_COUNT)],
        sync_dir=tmp_path / "stage-b-race",
    )

    claimed_sets = [_claimed_ids(result) for result in results]
    assert claimed_sets[0].isdisjoint(claimed_sets[1])
    assert claimed_sets[0] | claimed_sets[1] == expected_ids


async def test_process_claims_respect_stage_a_and_b_stale_boundaries(
    store: PostgresStore,
    migrated_pg_url: str,
    tmp_path: Path,
) -> None:
    """Claims recover rows older than one hour but not fresh owned rows."""
    stage_a_stale = await store.save_job(_job("stage-a-stale"))
    stage_a_fresh = await store.save_job(_job("stage-a-fresh"))
    await store.save_stage_a_error(stage_a_stale.job_id, "retry")
    await store.save_stage_a_error(stage_a_fresh.job_id, "retry")
    await _set_claim_age(
        store,
        stage_a_stale.job_id,
        stage="a",
        age=timedelta(minutes=61),
    )
    await _set_claim_age(
        store,
        stage_a_fresh.job_id,
        stage="a",
        age=timedelta(minutes=59),
    )
    stage_a_results = await run_store_process_race(
        dsn=migrated_pg_url,
        payloads=[
            {
                "operation": "claim_stage_a",
                "corpus": "unrated",
                "limit": CLAIM_JOB_COUNT,
            }
            for _ in range(WORKER_COUNT)
        ],
        sync_dir=tmp_path / "stage-a-stale-boundary",
    )
    stage_a_ids = set().union(*(_claimed_ids(result) for result in stage_a_results))
    assert stage_a_ids == {stage_a_stale.job_id}

    stage_b_stale_id = await store.save_job(_job("stage-b-stale"))
    stage_b_fresh_id = await store.save_job(_job("stage-b-fresh"))
    await store.save_stage_a(stage_b_stale_id.job_id, _stage_a())
    await store.save_stage_a(stage_b_fresh_id.job_id, _stage_a())
    await _set_claim_age(
        store,
        stage_b_stale_id.job_id,
        stage="b",
        age=timedelta(minutes=61),
    )
    await _set_claim_age(
        store,
        stage_b_fresh_id.job_id,
        stage="b",
        age=timedelta(minutes=59),
    )
    stage_b_results = await run_store_process_race(
        dsn=migrated_pg_url,
        payloads=[
            {"operation": "claim_stage_b", "limit": CLAIM_JOB_COUNT}
            for _ in range(WORKER_COUNT)
        ],
        sync_dir=tmp_path / "stage-b-stale-boundary",
    )
    stage_b_ids = set().union(*(_claimed_ids(result) for result in stage_b_results))
    assert stage_b_ids == {stage_b_stale_id.job_id}


async def test_reader_observes_committed_state_while_process_writer_is_open(
    store: PostgresStore,
    migrated_pg_url: str,
    tmp_path: Path,
) -> None:
    """A non-claiming reader does not wait on an uncommitted claim writer."""
    saved = await store.save_job(_job("reader-during-writer"))
    await store.save_stage_a_error(saved.job_id, "retry")
    entered_path = tmp_path / "writer-entered"
    release_path = tmp_path / "release-writer"
    payload = {
        "operation": "hold_stage_a_write",
        "job_id": saved.job_id,
        "entered_path": str(entered_path),
        "release_path": str(release_path),
    }
    worker_task = asyncio.create_task(
        run_store_process_race(
            dsn=migrated_pg_url,
            payloads=[payload],
            sync_dir=tmp_path / "reader-writer-race",
        )
    )
    await wait_for_process_signal(entered_path)

    try:
        candidates = await asyncio.wait_for(
            store.load_gate_candidates(corpus="unrated"),
            timeout=READER_TIMEOUT_SECONDS,
        )
    finally:
        release_path.touch()
        await worker_task

    assert [candidate.job.id for candidate in candidates] == [saved.job_id]
    assert await store.load_gate_candidates(corpus="unrated") == []
