"""SQLite contracts for pending, evaluation, batch, and threshold queries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobfeed.domain.models import QualityBand
from tests.support.sqlite_jobs_evaluations import (
    make_job,
    open_sqlite_store,
    stage_a,
    stage_b,
)

_TWO_THRESHOLD_ROWS = 2


async def _set_evaluation(lifecycle, job_id: str, **values: object) -> None:
    assignments = ", ".join(f"{name}=?" for name in values)
    async with lifecycle.connection() as connection:
        await connection.execute(
            f"UPDATE evaluations SET {assignments} WHERE job_id=?",
            (*values.values(), int(job_id)),
        )


async def _stage_b_status(lifecycle, job_id: str) -> str | None:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT stage_b_status FROM evaluations WHERE job_id=?", (int(job_id),)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return None if row is None else row[0]


async def test_pending_stage_a_corpora_retry_closed_quality_and_order(
    tmp_path: Path,
) -> None:
    """Stage A loaders preserve corpus, retry, liveness, filters, and tie-breaks."""
    lifecycle, store = await open_sqlite_store(tmp_path / "pending-a.db")
    now = datetime.now(UTC)
    try:
        unrated = await store.save_job(make_job("unrated", discovered_at=now))
        failed = await store.save_job(make_job("failed", discovered_at=now))
        completed = await store.save_job(make_job("completed", discovered_at=now))
        closed = await store.save_job(
            make_job("closed", discovered_at=now, closed_at=now)
        )
        over_cap = await store.save_job(make_job("over-cap", discovered_at=now))
        await store.save_stage_a_error(failed.job_id, "retry")
        await store.save_stage_a(completed.job_id, stage_a())
        for _ in range(3):
            await store.save_stage_a_error(over_cap.job_id, "terminal")

        assert [job.id for job in await store.load_pending_stage_a()] == [
            failed.job_id,
            unrated.job_id,
        ]
        assert [
            job.id for job in await store.load_pending_stage_a(corpus="failed")
        ] == [failed.job_id]
        assert [job.id for job in await store.load_pending_stage_a(corpus="all")] == [
            completed.job_id,
            failed.job_id,
            unrated.job_id,
        ]
        assert closed.job_id not in {
            job.id for job in await store.load_pending_stage_a(corpus="all")
        }
        assert await store.load_pending_stage_a(quality_bands=frozenset())
        assert (
            await store.load_pending_stage_a(
                quality_bands=frozenset({QualityBand.STUB.value})
            )
            == []
        )
        with pytest.raises(ValueError, match="corpus"):
            await store.load_pending_stage_a(corpus="unknown")
    finally:
        await lifecycle.close()


async def test_pending_stage_b_threshold_freshness_retry_and_status(
    tmp_path: Path,
) -> None:
    """Stage B loader keeps only under-cap null/error rows before its limit."""
    lifecycle, store = await open_sqlite_store(tmp_path / "pending-b.db")
    now = datetime.now(UTC)
    try:
        low = await store.save_job(make_job("low", discovered_at=now))
        high = await store.save_job(make_job("high", discovered_at=now))
        skipped = await store.save_job(make_job("skipped", discovered_at=now))
        over_cap = await store.save_job(make_job("over-cap", discovered_at=now))
        for saved, score in ((low, 40), (high, 90), (skipped, 95), (over_cap, 80)):
            await store.save_stage_a(saved.job_id, stage_a(score))
        await store.mark_stage_b_skipped(skipped.job_id)
        for _ in range(3):
            await store.save_stage_b_error(over_cap.job_id, "terminal")

        assert [
            job.id for job in await store.load_pending_stage_b(stage_a_threshold=70)
        ] == [high.job_id]
        assert {job.id for job in await store.load_pending_stage_b()} == {
            low.job_id,
            high.job_id,
        }
    finally:
        await lifecycle.close()


async def test_evaluation_reads_hydrate_left_join_json_and_stable_top_order(
    tmp_path: Path,
) -> None:
    """Evaluation detail/list/top preserve left join, JSON, and all tie-breaks."""
    lifecycle, store = await open_sqlite_store(tmp_path / "reads.db")
    now = datetime.now(UTC)
    try:
        bare = await store.save_job(make_job("bare", discovered_at=now))
        first = await store.save_job(make_job("first", discovered_at=now))
        second = await store.save_job(make_job("second", discovered_at=now))
        for saved in (first, second):
            await store.save_stage_a(saved.job_id, stage_a(80))
            await store.save_stage_b(saved.job_id, stage_b())

        bare_evaluation = await store.get_evaluation(bare.job_id)
        assert bare_evaluation is not None
        assert bare_evaluation.stage_a is None and bare_evaluation.stage_b is None
        assert await store.get_evaluation("999999") is None
        assert [row.job.id for row in await store.list_evaluated_jobs()] == [
            second.job_id,
            first.job_id,
        ]
        assert [row.job.id for row in await store.top_evaluated_jobs(min_score=80)] == [
            second.job_id,
            first.job_id,
        ]

        async with lifecycle.connection() as connection:
            await connection.execute("PRAGMA ignore_check_constraints=ON")
            await connection.execute(
                "UPDATE evaluations SET stage_b_fit_json='not-json' WHERE job_id=?",
                (int(first.job_id),),
            )
        with pytest.raises(json.JSONDecodeError):
            await store.get_evaluation(first.job_id)
    finally:
        await lifecycle.close()


async def test_score_batch_and_skip_batch_validate_before_mutation(
    tmp_path: Path,
) -> None:
    """Batch methods are strict on malformed IDs and skip completed rows."""
    lifecycle, store = await open_sqlite_store(tmp_path / "batch.db")
    try:
        first = await store.save_job(make_job("first"))
        second = await store.save_job(make_job("second"))
        bare = await store.save_job(make_job("bare"))
        await store.save_stage_a(first.job_id, stage_a(60))
        await store.save_stage_a(second.job_id, stage_a(90))
        await store.save_stage_b(second.job_id, stage_b())

        assert await store.get_stage_a_scores(
            [first.job_id, second.job_id, bare.job_id, first.job_id]
        ) == {first.job_id: 60, second.job_id: 90}
        assert await store.get_stage_a_scores([]) == {}
        with pytest.raises(ValueError):
            await store.get_stage_a_scores([first.job_id, "bad"])
        with pytest.raises(ValueError):
            await store.mark_stage_b_skipped_batch([first.job_id, "bad"])
        assert await _stage_b_status(lifecycle, first.job_id) is None

        await store.mark_stage_b_skipped_batch([first.job_id, second.job_id])
        assert await _stage_b_status(lifecycle, first.job_id) == (
            "skipped_below_threshold"
        )
        assert await _stage_b_status(lifecycle, second.job_id) == "completed"
    finally:
        await lifecycle.close()


async def test_threshold_mutations_and_preview_preserve_exact_guards(
    tmp_path: Path,
) -> None:
    """Threshold sync respects stale/retry guards and preview never mutates."""
    lifecycle, store = await open_sqlite_store(tmp_path / "threshold.db")
    now = datetime.now(UTC)
    stale = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    fresh = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    try:
        low_null = await store.save_job(make_job("low-null", discovered_at=now))
        low_stale = await store.save_job(make_job("low-stale", discovered_at=now))
        low_fresh = await store.save_job(make_job("low-fresh", discovered_at=now))
        high_skip = await store.save_job(make_job("high-skip", discovered_at=now))
        for saved, score in (
            (low_null, 40),
            (low_stale, 45),
            (low_fresh, 50),
            (high_skip, 90),
        ):
            await store.save_stage_a(saved.job_id, stage_a(score))
        await _set_evaluation(
            lifecycle,
            low_stale.job_id,
            stage_b_status="in_progress",
            stage_b_verdict=None,
            updated_at=stale,
        )
        await _set_evaluation(
            lifecycle,
            low_fresh.job_id,
            stage_b_status="in_progress",
            stage_b_verdict=None,
            updated_at=fresh,
        )
        await store.mark_stage_b_skipped(high_skip.job_id)

        preview = await store.preview_pending_stage_b_after_threshold_sync(
            stage_a_threshold=70
        )
        assert [job.id for job in preview] == [high_skip.job_id]
        assert await _stage_b_status(lifecycle, high_skip.job_id) == (
            "skipped_below_threshold"
        )
        assert await store.mark_stage_b_below_threshold(70) == _TWO_THRESHOLD_ROWS
        assert await store.mark_stage_b_below_threshold(70) == 0
        assert await _stage_b_status(lifecycle, low_fresh.job_id) == "in_progress"
        assert await store.reopen_stage_b_at_or_above_threshold(70) == 1
        assert await store.reopen_stage_b_at_or_above_threshold(70) == 0
        assert await _stage_b_status(lifecycle, high_skip.job_id) is None
    finally:
        await lifecycle.close()
