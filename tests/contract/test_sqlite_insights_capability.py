"""SQLite insights aggregation contracts."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jobfeed.adapters.store import _sqlite_insights
from jobfeed.domain.models_views import InsightsDay
from tests.support.sqlite_views_performance import (
    NOW,
    insert_job,
    open_views_performance,
    set_evaluation,
    set_unified_evaluation,
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
        assert result.detailed_reviewed_jobs == 0
        assert result.applied_jobs == 0
        assert result.verdict_distribution == {}
        assert result.decision_distribution == {}
        assert result.daily == []
    finally:
        await lifecycle.close()


async def test_insights_counts_only_completed_unified_evaluations(
    tmp_path: Path, monkeypatch
) -> None:
    """Canonical completion drives totals even when legacy stages disagree."""
    monkeypatch.setattr(_sqlite_insights, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "legacy-evaluated.db")
    try:
        quick = await insert_job(
            lifecycle,
            "quick",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            status="scored",
        )
        detailed = await insert_job(
            lifecycle,
            "detailed",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            status="scored",
        )
        await set_evaluation(lifecycle, quick, stage_a_score=70)
        await set_unified_evaluation(
            lifecycle,
            quick,
            match_score=70,
            match_tier="possible_match",
        )
        await set_evaluation(
            lifecycle,
            detailed,
            stage_a_score=80,
            verdict="apply",
            stage_b_status="completed",
        )
        await set_unified_evaluation(
            lifecycle,
            detailed,
            match_score=80,
            match_tier="strong_match",
            status="error",
        )
        async with lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE evaluations SET stage_a_status='completed', stage_a_at=NULL"
            )

        result = await store.insights_overview(window_days=7)

        assert result.evaluated_jobs == 1
        assert result.detailed_reviewed_jobs == 1
        assert result.verdict_distribution == {"possible_match": 1}
    finally:
        await lifecycle.close()


async def test_insights_cohort_totals_and_inclusive_windowed_days(
    tmp_path: Path, monkeypatch
) -> None:
    """Cohort totals and daily buckets use one inclusive UTC window."""
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
        await set_unified_evaluation(
            lifecycle,
            old,
            match_score=55,
            match_tier="weak_match",
        )
        await set_evaluation(
            lifecycle,
            boundary,
            stage_a_score=75,
            verdict="consider",
            stage_b_status="completed",
        )
        await set_unified_evaluation(
            lifecycle,
            boundary,
            match_score=75,
            match_tier="possible_match",
        )
        async with lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE evaluation_results SET evaluated_at = ? WHERE job_id = ?",
                (utc_text(NOW - timedelta(days=2)), boundary),
            )
            await connection.execute(
                "UPDATE evaluation_results SET evaluated_at = ? WHERE job_id = ?",
                (utc_text(NOW - timedelta(days=30)), old),
            )
            await connection.execute(
                """INSERT INTO job_status_history
                   (job_id, from_status, to_status, changed_at)
                   VALUES (?, 'new', 'applied', ?)""",
                (today, utc_text(NOW)),
            )

        result = await store.insights_overview(window_days=2)
        all_time = await store.insights_overview(window_days=None)

        assert result.total_jobs == 2
        assert result.ml_gate_passed_jobs == 1
        assert result.evaluated_jobs == 1
        assert result.applied_jobs == 1
        assert result.detailed_reviewed_jobs == 1
        assert result.verdict_distribution == {"possible_match": 1}
        assert result.decision_distribution == {
            "applied": 1,
            "results": 1,
        }
        assert [day.day.isoformat() for day in result.daily] == [
            (NOW - timedelta(days=2)).date().isoformat(),
            NOW.date().isoformat(),
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
        assert all_time.window_days is None
        assert all_time.total_jobs == 3
        assert all_time.ml_gate_passed_jobs == 2
        assert all_time.evaluated_jobs == 2
        assert all_time.applied_jobs == 1
        assert all_time.detailed_reviewed_jobs == 2
        assert all_time.verdict_distribution == {
            "possible_match": 1,
            "weak_match": 1,
        }
        assert all_time.decision_distribution == {
            "applied": 1,
            "ignored": 1,
            "results": 1,
        }
        assert [day.day.isoformat() for day in all_time.daily] == [
            (NOW - timedelta(days=30)).date().isoformat(),
            (NOW - timedelta(days=2)).date().isoformat(),
            NOW.date().isoformat(),
        ]
        assert future
    finally:
        await lifecycle.close()


async def test_insights_applied_matches_the_triage_applied_group(
    tmp_path: Path, monkeypatch
) -> None:
    """Applied totals include every status shown in the Triage Applied tab."""
    monkeypatch.setattr(_sqlite_insights, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "applied-status.db")
    try:
        current_applied = await insert_job(
            lifecycle,
            "current-applied",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            status="applied",
        )
        historical_applied = await insert_job(
            lifecycle,
            "historical-applied",
            discovered_at=utc_text(NOW),
            status="interviewing",
        )
        left_applied = await insert_job(
            lifecycle,
            "left-applied",
            discovered_at=utc_text(NOW),
            status="shortlisted",
        )
        async with lifecycle.connection() as connection:
            await connection.executemany(
                """INSERT INTO job_status_history
                   (job_id, from_status, to_status, changed_at)
                   VALUES (?, 'new', 'applied', ?)""",
                [
                    (
                        current_applied,
                        utc_text(NOW - timedelta(days=1)),
                    ),
                    (historical_applied, utc_text(NOW)),
                    (left_applied, utc_text(NOW)),
                ],
            )

        result = await store.insights_overview(window_days=7)

        assert result.applied_jobs == 2
        assert result.decision_distribution == {"applied": 2, "wait": 1}
        assert result.daily == [
            InsightsDay(
                day=(NOW - timedelta(days=1)).date(),
                discovered=1,
                evaluated=0,
                applied=1,
            ),
            InsightsDay(day=NOW.date(), discovered=2, evaluated=0, applied=1),
        ]
    finally:
        await lifecycle.close()


async def test_insights_window_filters_the_full_discovery_cohort(
    tmp_path: Path, monkeypatch
) -> None:
    """Seven- and 30-day insights use different discovery cohorts everywhere."""
    monkeypatch.setattr(_sqlite_insights, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "cohort.db")
    try:
        older = await insert_job(
            lifecycle,
            "older",
            discovered_at=utc_text(NOW - timedelta(days=8)),
            status="applied",
        )
        recent = await insert_job(
            lifecycle,
            "recent",
            discovered_at=utc_text(NOW - timedelta(days=1)),
            status="interviewing",
        )
        async with lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE jobs SET ml_gate_result='pass' WHERE id=?", (older,)
            )
            await connection.execute(
                """INSERT INTO job_status_history
                   (job_id, from_status, to_status, changed_at)
                   VALUES (?, 'new', 'applied', ?)""",
                (older, utc_text(NOW - timedelta(days=1))),
            )
            await connection.execute(
                """INSERT INTO job_status_history
                   (job_id, from_status, to_status, changed_at)
                   VALUES (?, 'new', 'applied', ?)""",
                (recent, utc_text(NOW)),
            )
        for job_id in (older, recent):
            await set_evaluation(
                lifecycle,
                job_id,
                stage_a_score=80,
                verdict="consider",
                stage_b_status="completed",
                stage_a_at=utc_text(NOW - timedelta(days=1)),
            )
            await set_unified_evaluation(
                lifecycle,
                job_id,
                match_score=80,
                match_tier="possible_match",
            )
        async with lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE evaluation_results SET evaluated_at=?",
                (utc_text(NOW - timedelta(days=1)),),
            )

        week = await store.insights_overview(window_days=7)
        month = await store.insights_overview(window_days=30)

        assert (week.total_jobs, month.total_jobs) == (1, 2)
        assert (week.ml_gate_passed_jobs, month.ml_gate_passed_jobs) == (0, 1)
        assert (week.evaluated_jobs, month.evaluated_jobs) == (1, 2)
        assert (week.applied_jobs, month.applied_jobs) == (1, 2)
        assert week.verdict_distribution == {"possible_match": 1}
        assert month.verdict_distribution == {"possible_match": 2}
        assert week.decision_distribution == {"applied": 1}
        assert month.decision_distribution == {"applied": 2}
        assert sum(day.applied for day in week.daily) == week.applied_jobs == 1
        assert sum(day.applied for day in month.daily) == month.applied_jobs == 2
    finally:
        await lifecycle.close()
