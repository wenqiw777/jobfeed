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
_CALLS_PER_MODEL = 2


def _run(
    run_id: str,
    source: str = "ats",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    status: str = "succeeded",
    **kw: object,
) -> PipelineRun:
    """Build a PipelineRun fixture with overrides.

    Args:
        run_id: Pipeline run identity.
        source: Source name (used for scan vs evaluate).
        started_at: Start timestamp.
        finished_at: Finish timestamp.
        status: Terminal status.
        **kw: Additional PipelineRun field overrides.

    Returns:
        Pipeline run fixture.
    """
    now = datetime.now(UTC)
    return PipelineRun(
        run_id=run_id,
        source=source,
        status=status,
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
    """Error rate treats failed runs as errors even when their counter is zero."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-ok", started_at=now - timedelta(hours=1), errors=0)
    )
    await store.record_pipeline_run(
        _run(
            "perf-err",
            started_at=now - timedelta(minutes=30),
            status="failed",
            errors=0,
        )
    )

    overview = await store.get_performance_overview(_WINDOW)

    expected_rate = 0.5
    assert overview.error_rate == pytest.approx(expected_rate)


async def test_overview_excludes_failed_durations_but_keeps_their_cost(
    store: PostgresStore,
) -> None:
    """Only succeeded runs shape durations; terminal runs still count toward cost."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-successful-scan",
            source="scan",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3) + timedelta(seconds=5),
            total_llm_cost_usd=1,
        )
    )
    await store.record_pipeline_run(
        _run(
            "perf-evaluation-retry",
            source="Evaluation Retry",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2) + timedelta(seconds=20),
            total_llm_cost_usd=2,
        )
    )
    await store.record_pipeline_run(
        _run(
            "perf-recovered-scan",
            source="scan",
            status="failed",
            started_at=now - timedelta(hours=1),
            finished_at=now,
            total_llm_cost_usd=3,
        )
    )

    overview = await store.get_performance_overview(_WINDOW)

    assert overview.avg_scan_duration_ms == pytest.approx(5000, rel=0.01)
    assert overview.avg_eval_duration_ms == pytest.approx(20000, rel=0.01)
    assert overview.total_llm_cost_usd == pytest.approx(6)


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


async def test_step_timings_ordering_is_stable(store: PostgresStore) -> None:
    """Rows sharing one created_at (batch insert) come back in id order."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-st-order", started_at=now - timedelta(hours=1))
    )
    # record_step_timings runs in one transaction, so NOW() (created_at) is
    # identical for every row -- only the id tiebreaker keeps insertion order.
    names = ["first", "second", "third", "fourth"]
    await store.record_step_timings(
        [
            StepTiming(
                run_id="perf-st-order",
                step_type="scan",
                step_name=name,
                elapsed_ms=float(i),
            )
            for i, name in enumerate(names)
        ]
    )

    timings = await store.get_step_timings(_WINDOW)

    assert [t.step_name for t in timings] == names


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
                model="perf-mini" if i < _CALLS_PER_MODEL else "perf-large",
                input_tokens=100 + i * 10,
                output_tokens=50 + i * 5,
                cost_usd=0.01,
                cached=False,
                latency_ms=100 + i * 100,
                timestamp=now,
                stage="a" if i < _CALLS_PER_MODEL else "b",
            )
        )

    stats = await store.get_llm_daily_stats(_WINDOW)

    today = [row for row in stats if row.model.startswith("perf-")]
    assert [(row.model, row.stage, row.call_count) for row in today] == [
        ("perf-large", "b", 2),
        ("perf-mini", "a", 2),
    ]
    assert all(row.p50_latency_ms > 0 for row in today)
    assert all(row.p95_latency_ms >= row.p50_latency_ms for row in today)
    assert all(row.avg_input_tokens > 0 for row in today)
    assert all(row.avg_output_tokens > 0 for row in today)


# ------------------------------------------------------------------
# get_funnel_stats
# ------------------------------------------------------------------


async def test_funnel_stats_empty(store: PostgresStore) -> None:
    """Empty database returns an empty list."""
    result = await store.get_funnel_stats(_WINDOW)

    assert result == []


async def test_funnel_stats_computes_totals(store: PostgresStore) -> None:
    """Funnel correctly derives total, after_filter, after_gate, scored.

    A stage="both" run has jobs_scored = stage_a_scored + stage_b_scored
    (double-counting jobs that reached Stage B); the funnel must use the
    unique per-job count (stage_a_scored) instead.
    """
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-funnel-1",
            source="evaluate",
            started_at=now - timedelta(hours=1),
            jobs_filtered=5,
            jobs_ml_gated=3,
            stage_a_scored=10,
            stage_b_scored=4,
            jobs_scored=14,  # stage_a + stage_b sum; must NOT leak into funnel
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
    expected_after_gate = 10  # stage_a_scored, not jobs_scored (14)
    assert f.after_gate == expected_after_gate
    assert f.scored == expected_after_gate


async def test_funnel_stats_prefers_gate_passed_counter(store: PostgresStore) -> None:
    """after_gate reports gate survivors, not the (limit-capped) scored count.

    A run that gated 500 candidates, passed 100, but scored only 50 (Stage A
    limit) must show after_gate=100; scored stays 50. Legacy rows with
    jobs_gate_passed=0 keep the scored-counter fallback (covered by
    test_funnel_stats_computes_totals).
    """
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-funnel-gate",
            source="evaluate",
            started_at=now - timedelta(hours=1),
            jobs_filtered=5,
            jobs_ml_gated=400,
            jobs_gate_passed=100,
            stage_a_scored=50,
            stage_b_scored=20,
            jobs_scored=70,
        )
    )

    funnels = await store.get_funnel_stats(_WINDOW)

    assert len(funnels) == 1
    f = funnels[0]
    expected_after_gate = 100  # gate survivors, not the 50 scored
    assert f.after_gate == expected_after_gate
    expected_after_filter = 500  # 400 failed + 100 passed
    assert f.after_filter == expected_after_filter
    expected_scored = 50
    assert f.scored == expected_scored
    assert f.total_candidates == expected_after_filter + 5


async def test_funnel_stats_stage_b_only_run(store: PostgresStore) -> None:
    """A Stage-B-only run (stage_a_scored=0) falls back to stage_b_scored."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run(
            "perf-funnel-b-only",
            source="evaluate",
            started_at=now - timedelta(hours=1),
            stage_a_scored=0,
            stage_b_scored=4,
            jobs_scored=4,
        )
    )

    funnels = await store.get_funnel_stats(_WINDOW)

    assert len(funnels) == 1
    f = funnels[0]
    expected_scored = 4
    assert f.total_candidates == expected_scored
    assert f.after_filter == expected_scored
    assert f.after_gate == expected_scored
    assert f.scored == expected_scored


async def test_funnel_stats_excludes_scan_runs(store: PostgresStore) -> None:
    """Scan runs contribute no funnel rows; only evaluate runs appear."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-funnel-scan", source="scan", started_at=now - timedelta(hours=2))
    )
    await store.record_pipeline_run(
        _run("perf-funnel-ats", source="ats", started_at=now - timedelta(hours=2))
    )
    await store.record_pipeline_run(
        _run(
            "perf-funnel-eval",
            source="evaluate",
            started_at=now - timedelta(hours=1),
            stage_a_scored=2,
            jobs_scored=2,
        )
    )

    funnels = await store.get_funnel_stats(_WINDOW)

    assert [f.run_id for f in funnels] == ["perf-funnel-eval"]


async def test_funnel_stats_empty_for_scan_only_window(store: PostgresStore) -> None:
    """A window holding only scan runs yields an empty funnel, not zeros."""
    now = datetime.now(UTC)
    await store.record_pipeline_run(
        _run("perf-scan-only", source="scan", started_at=now - timedelta(hours=1))
    )

    funnels = await store.get_funnel_stats(_WINDOW)

    assert funnels == []
