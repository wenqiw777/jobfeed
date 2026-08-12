"""Absolute Task 3 view and insights budgets at the frozen 56k scale."""

# ruff: noqa: PLR2004

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from jobfeed.adapters.store import _sqlite_insights
from jobfeed.domain.models_views import JobsViewQuery
from tests.support.sqlite_views_performance import (
    NOW,
    open_views_performance,
    utc_text,
)

_JOB_COUNT = 56_000
_SAMPLES = 30
_P95_BUDGET_SECONDS = 2.0


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.95
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


async def test_56k_views_and_insights_p95_stay_under_absolute_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Bounded view and full insights aggregation each stay below two seconds."""
    monkeypatch.setattr(_sqlite_insights, "_utc_now", lambda: NOW)
    lifecycle, store = await open_views_performance(tmp_path / "benchmark.db")
    try:
        async with lifecycle.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.executemany(
                """INSERT INTO jobs (
                       platform, canonical_id, url, title, company, location,
                       discovered_at, company_norm, title_norm, location_norm
                   ) VALUES ('test', ?, ?, 'Engineer', 'Example', 'Remote',
                             ?, 'example', 'engineer', 'remote')""",
                (
                    (str(index), f"https://example.test/{index}", utc_text(NOW))
                    for index in range(_JOB_COUNT)
                ),
            )
            await connection.commit()

        view_samples: list[float] = []
        insights_samples: list[float] = []
        for _ in range(_SAMPLES):
            started = perf_counter()
            result = await store.query_jobs_view(JobsViewQuery(tab="all"))
            view_samples.append(perf_counter() - started)
            assert len(result.rows) == 50
            assert result.total == _JOB_COUNT

            started = perf_counter()
            insights = await store.insights_overview(window_days=7)
            insights_samples.append(perf_counter() - started)
            assert insights.total_jobs == _JOB_COUNT

        assert _p95(view_samples) < _P95_BUDGET_SECONDS
        assert _p95(insights_samples) < _P95_BUDGET_SECONDS
    finally:
        await lifecycle.close()
