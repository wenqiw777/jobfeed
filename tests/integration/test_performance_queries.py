"""Performance store query integration tests against PostgreSQL.

Seeds pipeline runs, step timings, and LLM usage rows, then verifies
that the four performance query methods return correct aggregates and
shapes. All tests use the session-scoped migrated database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.models_llm import LLMUsage
from jobfeed.domain.models_perf import StepTiming

pytestmark = pytest.mark.postgres

_WINDOW = 30


def _run(
    run_id: str,
    source: str = "ats",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    **kw: object,
) -> PipelineRun:
    """Build a PipelineRun fixture with overrides.

    Args:
        run_id: Pipeline run identity.
        source: Source name (used for scan vs evaluate).
        started_at: Start timestamp.
        finished_at: Finish timestamp.
        **kw: Additional PipelineRun field overrides.

    Returns:
        Pipeline run fixture.
    """
    now = datetime.now(UTC)
    return PipelineRun(
        run_id=run_id,
        source=source,
        status="succeeded",
        started_at=started_at or now,
        finished_at=finished_at or (started_at or now) + timedelta(seconds=10),
        **kw,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------
# get_performance_overview
# ------------------------------------------------------------------


async def test_overview_returns_zeros_on_empty_db(store: PostgresStore) -> None:
    """An empty database yields all-zero overview with None deltas."""
    overview = await store.get_performance_overview(_WINDOW)

    assert overview.avg_scan_duration_ms == 0.0
    assert overview.avg_eval_duration_ms == 0.0
    assert overview.total_llm_cost_usd == 0.0
    assert overview.error_rate == 0.0
    assert overview.scan_duration_delta is None
    assert overview.eval_duration_delta is None
    assert overview.cost_delta is None
    assert overview.error_rate_delta is None


async def test_overview_computes_scan_and_eval_averages(
    store: PostgresStore,
) -> None:
    """Scan and evaluate runs produce separate duration averages."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-scan-1",
            source="scan",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2) + timedelta(seconds=5),
        )
    )
    await store.record_pipeline_run(
        _run(
            "perf-eval-1",
            source="evaluate",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1) + timedelta(seconds=20),
        )
    )

    overview = await store.get_performance_overview(_WINDOW)

    expected_scan_ms = 5000.0
    expected_eval_ms = 20000.0
    assert overview.avg_scan_duration_ms == pytest.approx(expected_scan_ms, rel=0.01)
    assert overview.avg_eval_duration_ms == pytest.approx(expected_eval_ms, rel=0.01)


async def test_overview_error_rate(store: PostgresStore) -> None:
    """Error rate is fraction of runs with errors > 0."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-ok", started_at=now - timedelta(hours=1), errors=0)
    )
    await store.record_pipeline_run(
        _run("perf-err", started_at=now - timedelta(minutes=30), errors=3)
    )

    overview = await store.get_performance_overview(_WINDOW)

    expected_rate = 0.5
    assert overview.error_rate == pytest.approx(expected_rate)


async def test_overview_cost_total(store: PostgresStore) -> None:
    """Total LLM cost sums across all runs in the window."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-cost-1",
            started_at=now - timedelta(hours=2),
            total_llm_cost_usd=1.50,
        )
    )
    await store.record_pipeline_run(
        _run(
            "perf-cost-2",
            started_at=now - timedelta(hours=1),
            total_llm_cost_usd=0.50,
        )
    )

    overview = await store.get_performance_overview(_WINDOW)

    expected_cost = 2.0
    assert overview.total_llm_cost_usd == pytest.approx(expected_cost)


# ------------------------------------------------------------------
# get_step_timings
# ------------------------------------------------------------------


async def test_step_timings_returns_empty(store: PostgresStore) -> None:
    """Empty database returns an empty list."""
    result = await store.get_step_timings(_WINDOW)

    assert result == []


async def test_step_timings_filtered_by_type(store: PostgresStore) -> None:
    """step_type filter narrows results."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-st-1", started_at=now - timedelta(hours=1))
    )
    await store.record_step_timing(
        StepTiming(run_id="perf-st-1", step_type="scan", step_name="gh", elapsed_ms=100)
    )
    await store.record_step_timing(
        StepTiming(
            run_id="perf-st-1", step_type="evaluate", step_name="a", elapsed_ms=200
        )
    )

    scan_only = await store.get_step_timings(_WINDOW, step_type="scan")

    assert len(scan_only) == 1
    assert scan_only[0].step_type == "scan"
    assert scan_only[0].step_name == "gh"


async def test_step_timings_all_types(store: PostgresStore) -> None:
    """Without filter, all step types are returned."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-st-all", started_at=now - timedelta(hours=1))
    )
    await store.record_step_timing(
        StepTiming(
            run_id="perf-st-all", step_type="scan", step_name="gh", elapsed_ms=100
        )
    )
    await store.record_step_timing(
        StepTiming(
            run_id="perf-st-all",
            step_type="evaluate",
            step_name="a",
            elapsed_ms=200,
            is_error=True,
        )
    )

    all_timings = await store.get_step_timings(_WINDOW)

    expected_count = 2
    assert len(all_timings) == expected_count
    assert all_timings[1].is_error is True


# ------------------------------------------------------------------
# get_llm_daily_stats
# ------------------------------------------------------------------


async def test_llm_daily_stats_empty(store: PostgresStore) -> None:
    """Empty database returns an empty list."""
    result = await store.get_llm_daily_stats(_WINDOW)

    assert result == []


async def test_llm_daily_stats_computes_percentiles(
    store: PostgresStore,
) -> None:
    """Daily stats compute latency percentiles and token averages."""
    now = datetime.now(UTC)
    for i in range(4):
        await store.record_llm_usage(
            LLMUsage(
                model="test",
                input_tokens=100 + i * 10,
                output_tokens=50 + i * 5,
                cost_usd=0.01,
                cached=False,
                latency_ms=100 + i * 100,
                timestamp=now - timedelta(hours=i),
            )
        )

    stats = await store.get_llm_daily_stats(_WINDOW)

    assert len(stats) >= 1
    today = stats[-1]
    assert today.p50_latency_ms > 0
    assert today.p95_latency_ms >= today.p50_latency_ms
    assert today.avg_input_tokens > 0
    assert today.avg_output_tokens > 0


# ------------------------------------------------------------------
# get_funnel_stats
# ------------------------------------------------------------------


async def test_funnel_stats_empty(store: PostgresStore) -> None:
    """Empty database returns an empty list."""
    result = await store.get_funnel_stats(_WINDOW)

    assert result == []


async def test_funnel_stats_computes_totals(store: PostgresStore) -> None:
    """Funnel correctly derives total, after_filter, after_gate, scored."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-funnel-1",
            started_at=now - timedelta(hours=1),
            jobs_filtered=5,
            jobs_ml_gated=3,
            jobs_scored=10,
        )
    )

    funnels = await store.get_funnel_stats(_WINDOW)

    assert len(funnels) == 1
    f = funnels[0]
    assert f.run_id == "perf-funnel-1"
    expected_total = 18  # 5 + 3 + 10
    assert f.total_candidates == expected_total
    expected_after_filter = 13  # 3 + 10
    assert f.after_filter == expected_after_filter
    expected_after_gate = 10
    assert f.after_gate == expected_after_gate
    assert f.scored == expected_after_gate
