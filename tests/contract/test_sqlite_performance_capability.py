"""SQLite performance persistence and aggregation contracts."""

# ruff: noqa: PLR2004

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from jobfeed.adapters.store import _sqlite_performance
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.models_perf import StepTiming
from tests.support.sqlite_views_performance import (
    NOW,
    insert_run,
    open_views_performance,
    rows,
    utc_text,
)


async def test_step_timing_write_uses_database_time_and_batch_is_atomic(
    tmp_path: Path,
) -> None:
    """Timing IDs preserve batch order and any FK failure rolls back the batch."""
    lifecycle, store = await open_views_performance(tmp_path / "writes.db")
    try:
        await insert_run(
            lifecycle,
            PipelineRun(run_id="run", started_at=NOW, source="scan"),
        )
        await store.record_step_timing(
            StepTiming(
                run_id="run",
                step_type="scan",
                step_name="fetch",
                elapsed_ms=12.5,
                created_at=NOW - timedelta(days=300),
            )
        )
        series = await store.get_step_timings(window_days=1)
        assert [item.step_name for item in series] == ["fetch"]
        assert series[0].created_at != NOW - timedelta(days=300)
        assert series[0].created_at.tzinfo is not None

        with pytest.raises(sqlite3.IntegrityError):
            await store.record_step_timings(
                [
                    StepTiming(
                        run_id="run",
                        step_type="scan",
                        step_name="rolled-back",
                        elapsed_ms=1,
                    ),
                    StepTiming(
                        run_id="missing",
                        step_type="scan",
                        step_name="invalid",
                        elapsed_ms=2,
                    ),
                ]
            )
        stored = await rows(
            lifecycle,
            "SELECT step_name FROM step_timings ORDER BY id",
        )
        assert [tuple(row) for row in stored] == [("fetch",)]

        await store.record_step_timings([])
        assert len(await store.get_step_timings(window_days=1)) == 1
    finally:
        await lifecycle.close()


async def test_performance_overview_windows_deltas_and_empty_semantics(
    tmp_path: Path, monkeypatch
) -> None:
    """Terminal runs keep cost/error accounting separate from duration KPIs."""
    monkeypatch.setattr(_sqlite_performance, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "overview.db")
    try:
        empty = await store.get_performance_overview(7)
        assert empty == _sqlite_performance.zero_overview()
        runs = (
            PipelineRun(
                run_id="current-scan",
                started_at=NOW - timedelta(days=1),
                finished_at=NOW,
                source="scan",
                total_llm_cost_usd=3,
                errors=1,
                status="succeeded",
            ),
            PipelineRun(
                run_id="current-eval",
                started_at=NOW,
                finished_at=NOW,
                source="evaluate",
                total_llm_cost_usd=5,
                errors=0,
                status="succeeded",
            ),
            PipelineRun(
                run_id="current-evaluation",
                started_at=NOW - timedelta(seconds=1),
                finished_at=NOW,
                source="Evaluation Retry",
                status="succeeded",
            ),
            PipelineRun(
                run_id="previous-scan",
                started_at=NOW - timedelta(days=8),
                finished_at=NOW - timedelta(days=8) + timedelta(seconds=2),
                source="scan",
                total_llm_cost_usd=2,
                errors=0,
                status="succeeded",
            ),
            PipelineRun(
                run_id="running-ignored",
                started_at=NOW,
                source="scan",
                status="running",
            ),
            PipelineRun(
                run_id="failed-stale-eval",
                started_at=NOW - timedelta(days=6),
                finished_at=NOW,
                source="evaluate",
                total_llm_cost_usd=1,
                status="failed",
            ),
            PipelineRun(
                run_id="failed-stale-scan",
                started_at=NOW - timedelta(days=5),
                finished_at=NOW,
                source="scan",
                total_llm_cost_usd=4,
                status="failed",
            ),
        )
        for run in runs:
            await insert_run(lifecycle, run)

        overview = await store.get_performance_overview(7)

        assert overview.avg_scan_duration_ms == 86_400_000
        assert overview.avg_eval_duration_ms == 500
        assert overview.total_llm_cost_usd == 13
        assert overview.error_rate == pytest.approx(3 / 5)
        assert overview.scan_duration_delta == 43_199
        assert overview.eval_duration_delta is None
        assert overview.cost_delta == 5.5
        assert overview.error_rate_delta is None
    finally:
        await lifecycle.close()


async def test_step_series_llm_percentiles_and_funnel_semantics(
    tmp_path: Path, monkeypatch
) -> None:
    """Inclusive cutoffs, continuous percentiles, and derived funnel stages hold."""
    monkeypatch.setattr(_sqlite_performance, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "queries.db")
    try:
        for run in (
            PipelineRun(
                run_id="eval-new",
                started_at=NOW,
                finished_at=NOW,
                source="evaluate",
                jobs_discovered=10,
                jobs_filtered=3,
                jobs_ml_gated=2,
                jobs_gate_passed=4,
                stage_a_scored=5,
                stage_b_scored=6,
                jobs_scored=7,
                status="succeeded",
            ),
            PipelineRun(
                run_id="eval-boundary",
                started_at=NOW - timedelta(days=2),
                finished_at=NOW,
                source="evaluate",
                jobs_discovered=1,
                status="succeeded",
            ),
            PipelineRun(
                run_id="scan-ignored",
                started_at=NOW,
                finished_at=NOW,
                source="scan",
                status="succeeded",
            ),
        ):
            await insert_run(lifecycle, run)
        async with lifecycle.connection() as connection:
            await connection.executemany(
                """
                INSERT INTO step_timings
                    (run_id, step_type, step_name, elapsed_ms, is_error, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "eval-boundary",
                        "evaluate",
                        "a",
                        1,
                        0,
                        utc_text(NOW - timedelta(days=2)),
                    ),
                    ("eval-new", "evaluate", "b", 2, 1, utc_text(NOW)),
                    (
                        "scan-ignored",
                        "scan",
                        "c",
                        3,
                        0,
                        utc_text(NOW - timedelta(days=3)),
                    ),
                ],
            )
            await connection.executemany(
                """
                INSERT INTO llm_usage
                    (model, stage, input_tokens, output_tokens, cost_usd, cached,
                     latency_ms, timestamp, run_id)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?, 'eval-new')
                """,
                [
                    ("gpt-mini", "a", 10, 20, 10, utc_text(NOW - timedelta(days=2))),
                    ("gpt-mini", "a", 20, 30, 20, utc_text(NOW)),
                    ("gpt-mini", "a", 30, 40, 30, utc_text(NOW)),
                    ("gpt-large", "b", 40, 50, 40, utc_text(NOW)),
                    ("gpt-large", "b", 50, 60, 50, utc_text(NOW)),
                    ("gpt-unified", "evaluation", 60, 70, 60, utc_text(NOW)),
                    ("gpt-old", None, 999, 999, 999, utc_text(NOW - timedelta(days=3))),
                ],
            )

        series = await store.get_step_timings(2, step_type="evaluate")
        daily = await store.get_llm_daily_stats(2)
        funnel = await store.get_funnel_stats(2)

        assert [(item.step_name, item.is_error) for item in series] == [
            ("a", False),
            ("b", True),
        ]
        assert [item.day for item in daily] == [
            (NOW - timedelta(days=2)).date().isoformat(),
            NOW.date().isoformat(),
            NOW.date().isoformat(),
            NOW.date().isoformat(),
        ]
        assert [(item.model, item.stage) for item in daily] == [
            ("gpt-mini", "a"),
            ("gpt-large", "b"),
            ("gpt-mini", "a"),
            ("gpt-unified", "evaluation"),
        ]
        assert daily[0].p50_latency_ms == daily[0].p95_latency_ms == 10
        assert daily[1].p50_latency_ms == 45
        assert daily[1].p95_latency_ms == pytest.approx(49.5)
        assert daily[0].call_count == 1
        assert [item.call_count for item in daily] == [1, 2, 2, 1]
        assert daily[1].avg_input_tokens == 45
        assert [item.run_id for item in funnel] == ["eval-new", "eval-boundary"]
        assert funnel[0].total_candidates == 12
        assert funnel[0].after_gate == 7
        assert funnel[0].scored == 7
        assert funnel[0].after_filter == 9
    finally:
        await lifecycle.close()
