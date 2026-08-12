"""SQLite insights aggregation contracts."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jobfeed.adapters.store import _sqlite_insights
from tests.support.sqlite_views_performance import (
    NOW,
    insert_job,
    open_views_performance,
    set_evaluation,
    utc_text,
)


async def test_insights_empty_database_has_explicit_zero_semantics(
    tmp_path: Path, monkeypatch
) -> None:
    """Empty insights return zero totals, empty mappings, and no fake days."""
    monkeypatch.setattr(_sqlite_insights, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "empty.db")
    try:
        result = await store.insights_overview(window_days=7)

        assert result.window_days == 7
        assert result.total_jobs == 0
        assert result.ml_gate_passed_jobs == 0
        assert result.evaluated_jobs == 0
        assert result.applied_jobs == 0
        assert result.verdict_distribution == {}
        assert result.status_distribution == {}
        assert result.daily == []
    finally:
        await lifecycle.close()


async def test_insights_all_time_totals_and_inclusive_windowed_days(
    tmp_path: Path, monkeypatch
) -> None:
    """Totals stay all-time while daily buckets use one inclusive UTC window."""
    monkeypatch.setattr(_sqlite_insights, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "insights.db")
    try:
        old = await insert_job(
            lifecycle,
            "old",
            discovered_at=utc_text(NOW - timedelta(days=30)),
            status="archived",
        )
        boundary = await insert_job(
            lifecycle,
            "boundary",
            discovered_at=utc_text(NOW - timedelta(days=2)),
            status="scored",
        )
        today = await insert_job(
            lifecycle,
            "today",
            discovered_at=utc_text(NOW),
            status="applied",
        )
        future = await insert_job(
            lifecycle,
            "future",
            discovered_at=utc_text(NOW + timedelta(seconds=1)),
            status="new",
        )
        async with lifecycle.connection() as connection:
            await connection.executemany(
                "UPDATE jobs SET ml_gate_result = ? WHERE id = ?",
                [("pass", old), ("pass", boundary), ("fail", today)],
            )
        await set_evaluation(
            lifecycle,
            old,
            stage_a_score=60,
            verdict=None,
            stage_b_status="skipped_below_threshold",
        )
        await set_evaluation(
            lifecycle,
            boundary,
            stage_a_score=75,
            verdict="consider",
            stage_b_status="completed",
        )
        async with lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE evaluations SET stage_a_at = ? WHERE job_id = ?",
                (utc_text(NOW - timedelta(days=2)), boundary),
            )
            await connection.execute(
                "UPDATE evaluations SET stage_a_at = ? WHERE job_id = ?",
                (utc_text(NOW - timedelta(days=30)), old),
            )
            await connection.execute(
                "INSERT INTO applied (job_id, applied_at) VALUES (?, ?)",
                (today, utc_text(NOW)),
            )

        result = await store.insights_overview(window_days=2)

        assert result.total_jobs == 4
        assert result.ml_gate_passed_jobs == 2
        assert result.evaluated_jobs == 2
        assert result.applied_jobs == 1
        assert result.verdict_distribution == {
            "below_threshold": 1,
            "consider": 1,
        }
        assert result.status_distribution == {
            "applied": 1,
            "archived": 1,
            "new": 1,
            "scored": 1,
        }
        assert [day.day.isoformat() for day in result.daily] == [
            "2026-08-10",
            "2026-08-12",
        ]
        assert (
            result.daily[0].discovered,
            result.daily[0].evaluated,
            result.daily[0].applied,
        ) == (1, 1, 0)
        assert (
            result.daily[1].discovered,
            result.daily[1].evaluated,
            result.daily[1].applied,
        ) == (1, 0, 1)
        assert future
    finally:
        await lifecycle.close()
