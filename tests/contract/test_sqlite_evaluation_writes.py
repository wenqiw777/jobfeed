"""SQLite contracts for Stage A and Stage B evaluation writes."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store import _sqlite_evaluations
from tests.support.sqlite_jobs_evaluations import (
    make_job,
    open_sqlite_store,
    stage_a,
    stage_b,
)


async def _evaluation_row(lifecycle, job_id: str):
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            "SELECT * FROM evaluations WHERE job_id=?", (int(job_id),)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row


async def test_stage_a_success_atomically_advances_new_status(tmp_path: Path) -> None:
    """Stage A upsert and the one-time new-to-scored history commit together."""
    lifecycle, store = await open_sqlite_store(tmp_path / "stage-a.db")
    try:
        saved = await store.save_job(make_job("stage-a"))
        await store.save_stage_a(saved.job_id, stage_a(91))
        row = await _evaluation_row(lifecycle, saved.job_id)
        assert row is not None
        assert (row["stage_a_score"], row["stage_a_status"], row["stage_a_error"]) == (
            91,
            "completed",
            None,
        )
        first_stage_a_at = row["stage_a_at"]
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT status FROM job_status WHERE job_id=?", (int(saved.job_id),)
            )
            assert await cursor.fetchone() == ("scored",)
            await cursor.close()
            cursor = await connection.execute(
                "SELECT from_status, to_status, reason FROM job_status_history "
                "WHERE job_id=? ORDER BY id",
                (int(saved.job_id),),
            )
            assert await cursor.fetchall() == [
                (None, "new", None),
                ("new", "scored", "auto_scored"),
            ]
            await cursor.close()

        await store.mark_stage_b_skipped(saved.job_id)
        await store.save_stage_a(saved.job_id, stage_a(92))
        row = await _evaluation_row(lifecycle, saved.job_id)
        assert row is not None
        assert row["stage_a_at"] == first_stage_a_at
        assert row["stage_b_status"] is None and row["stage_b_error"] is None
    finally:
        await lifecycle.close()


async def test_stage_a_status_failure_rolls_back_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure between Stage A and status history leaves no partial write."""
    lifecycle, store = await open_sqlite_store(tmp_path / "stage-a-rollback.db")
    saved = await store.save_job(make_job("rollback"))

    async def reject_history(*_args: object) -> None:
        raise RuntimeError("injected scored history failure")

    monkeypatch.setattr(_sqlite_evaluations, "_insert_scored_history", reject_history)
    try:
        with pytest.raises(RuntimeError, match="injected scored history"):
            await store.save_stage_a(saved.job_id, stage_a())
        assert await _evaluation_row(lifecycle, saved.job_id) is None
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT status FROM job_status WHERE job_id=?", (int(saved.job_id),)
            )
            assert await cursor.fetchone() == ("new",)
            await cursor.close()
    finally:
        await lifecycle.close()


async def test_stage_errors_increment_and_missing_jobs_keep_fk_errors(
    tmp_path: Path,
) -> None:
    """Retry errors count each call and never convert FK failures to success."""
    lifecycle, store = await open_sqlite_store(tmp_path / "errors.db")
    try:
        saved = await store.save_job(make_job("errors"))
        await store.save_stage_a_error(saved.job_id, "first-a")
        await store.save_stage_a_error(saved.job_id, "latest-a")
        await store.save_stage_b_error(saved.job_id, "first-b")
        await store.save_stage_b_error(saved.job_id, "latest-b")
        row = await _evaluation_row(lifecycle, saved.job_id)
        assert row is not None
        assert (row["stage_a_error_count"], row["stage_a_error"]) == (2, "latest-a")
        assert (row["stage_b_error_count"], row["stage_b_error"]) == (2, "latest-b")
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await store.save_stage_b_error("999999", "missing")
        with pytest.raises(ValueError):
            await store.save_stage_a_error("invalid", "bad id")
    finally:
        await lifecycle.close()


async def test_stage_b_json_is_canonical_unicode_and_first_time_is_preserved(
    tmp_path: Path,
) -> None:
    """Structured Stage B blocks persist stable bytes and hydrate unchanged."""
    lifecycle, store = await open_sqlite_store(tmp_path / "stage-b.db")
    raw = {
        "verdict": {"理由": "匹配", "recommendation": "consider"},
        "jd_summary": {"role_in_3_lines": "平台工程"},
        "fit_analysis": {
            "gaps": [],
            "score_0_100": 88,
            "strong_match": [{"evidence_from_resume": "七年", "requirement": "Python"}],
        },
        "resume_hooks": {"supporting": ["规模"], "lead_with": "可靠性"},
    }
    try:
        saved = await store.save_job(make_job("stage-b"))
        await store.save_stage_a(saved.job_id, stage_a())
        await store.save_stage_b(saved.job_id, stage_b(raw_blocks=raw))
        first = await _evaluation_row(lifecycle, saved.job_id)
        assert first is not None
        assert first["stage_b_verdict_json"] == json.dumps(
            raw["verdict"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        assert "\\u" not in first["stage_b_verdict_json"]
        first_stage_b_at = first["stage_b_at"]

        await store.save_stage_b(saved.job_id, stage_b(raw_blocks=raw))
        second = await _evaluation_row(lifecycle, saved.job_id)
        assert second is not None and second["stage_b_at"] == first_stage_b_at
        hydrated = await store.get_evaluation(saved.job_id)
        assert hydrated is not None and hydrated.stage_b is not None
        assert hydrated.stage_b.raw_blocks == raw
    finally:
        await lifecycle.close()


async def test_nonfinite_stage_b_json_fails_without_damaging_prior_value(
    tmp_path: Path,
) -> None:
    """NaN JSON is rejected before the previous completed result can change."""
    lifecycle, store = await open_sqlite_store(tmp_path / "invalid-json.db")
    try:
        saved = await store.save_job(make_job("invalid-json"))
        await store.save_stage_b(saved.job_id, stage_b())
        previous = await _evaluation_row(lifecycle, saved.job_id)
        invalid = stage_b(raw_blocks={"fit_analysis": {"score_0_100": float("nan")}})
        with pytest.raises(ValueError, match="JSON"):
            await store.save_stage_b(saved.job_id, invalid)
        current = await _evaluation_row(lifecycle, saved.job_id)
        assert current is not None and previous is not None
        assert current["stage_b_fit_json"] == previous["stage_b_fit_json"]
    finally:
        await lifecycle.close()


async def test_skip_is_idempotent_and_never_erases_completed_stage_b(
    tmp_path: Path,
) -> None:
    """Single-job skip is a no-op for completed Stage B output."""
    lifecycle, store = await open_sqlite_store(tmp_path / "skip.db")
    try:
        saved = await store.save_job(make_job("skip"))
        await store.save_stage_a(saved.job_id, stage_a())
        await store.mark_stage_b_skipped(saved.job_id)
        await store.mark_stage_b_skipped(saved.job_id)
        row = await _evaluation_row(lifecycle, saved.job_id)
        assert row is not None and row["stage_b_status"] == "skipped_below_threshold"
        await store.save_stage_b(saved.job_id, stage_b())
        await store.mark_stage_b_skipped(saved.job_id)
        row = await _evaluation_row(lifecycle, saved.job_id)
        assert row is not None and row["stage_b_status"] == "completed"
    finally:
        await lifecycle.close()
