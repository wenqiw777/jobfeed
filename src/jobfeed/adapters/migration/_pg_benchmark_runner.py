"""PostgreSQL benchmark runner over the real store and service methods."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from jobfeed.adapters.migration._baseline_workload import BenchmarkQuery
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.models import JobPosting, JobStatus
from jobfeed.domain.models_status import StatusFilter
from jobfeed.domain.models_views import JobsViewQuery
from jobfeed.services.jobs_view import JobsViewService


@dataclass(frozen=True, kw_only=True)
class StoreBenchmarkResult:
    """Recorded timings and final cardinality for one store operation."""

    samples_ms: list[float]
    row_count: int


@dataclass(frozen=True, kw_only=True)
class _Seeds:
    job_id: str
    jobs: tuple[JobPosting, ...]
    twin_keys: tuple[tuple[str, str], ...]


async def _seed_inputs(store: PostgresStore, limit: int) -> _Seeds:
    jobs = tuple(await store.list_jobs(limit=limit))
    page = await store.query_jobs_view(JobsViewQuery(tab="all", limit=limit))
    keys = tuple(
        dict.fromkeys(
            (row.company_norm, row.title_norm)
            for row in page.rows
            if row.company_norm and row.title_norm
        )
    )
    if not jobs or not page.rows or not keys:
        raise ValueError("benchmark seed corpus is empty or has no non-blank twin key")
    job_id = str(jobs[0].id) if jobs and jobs[0].id is not None else "0"
    return _Seeds(job_id=job_id, jobs=jobs, twin_keys=keys)


def _count_rows(result: object) -> int:
    if result is None:
        return 0
    if isinstance(result, tuple) and result and isinstance(result[0], list):
        return len(result[0])
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        return len(result)
    return 1


def _views_operation_call(
    store: PostgresStore,
    detail: JobsViewService,
    query: BenchmarkQuery,
    seeds: _Seeds,
) -> Callable[[], Awaitable[object]]:
    params = query.params
    operation = query.operation
    if operation == "jobs_view_list":
        return lambda: store.list_jobs(limit=params["limit"])
    if operation == "job_detail":
        return lambda: detail.get_job_detail(seeds.job_id)
    if operation == "status_queue":
        return lambda: store.list_statuses(StatusFilter(limit=params["limit"]))
    if operation == "query_jobs_view":
        return lambda: store.query_jobs_view(
            JobsViewQuery(tab="all", limit=params["limit"])
        )
    if operation == "list_twin_rows_by_status":
        statuses = tuple(status.value for status in JobStatus)
        return lambda: store.list_twin_rows_by_status(
            seeds.twin_keys, statuses=statuses, limit=params["limit"]
        )
    if operation == "list_twin_statuses":
        return lambda: store.list_twin_statuses(seeds.job_id)
    raise ValueError(f"unknown PostgreSQL view benchmark operation: {operation}")


def _metrics_operation_call(
    store: PostgresStore,
    query: BenchmarkQuery,
) -> Callable[[], Awaitable[object]]:
    params = query.params
    operation = query.operation
    if operation == "list_pipeline_runs":
        return lambda: store.list_pipeline_runs(limit=params["limit"])
    if operation == "insights_overview":
        return lambda: store.insights_overview(window_days=params["window_days"])
    if operation == "get_performance_overview":
        return lambda: store.get_performance_overview(params["window_days"])
    if operation == "get_step_timings":
        return lambda: store.get_step_timings(params["window_days"])
    if operation == "get_llm_daily_stats":
        return lambda: store.get_llm_daily_stats(params["window_days"])
    if operation == "get_funnel_stats":
        return lambda: store.get_funnel_stats(params["window_days"])
    raise ValueError(f"unknown PostgreSQL benchmark operation: {operation}")


def _operation_call(
    store: PostgresStore,
    detail: JobsViewService,
    query: BenchmarkQuery,
    seeds: _Seeds,
) -> Callable[[], Awaitable[object]]:
    if query.operation in {
        "jobs_view_list",
        "job_detail",
        "status_queue",
        "query_jobs_view",
        "list_twin_rows_by_status",
        "list_twin_statuses",
    }:
        return _views_operation_call(store, detail, query, seeds)
    return _metrics_operation_call(store, query)


async def run_postgres_store_benchmarks(
    dsn: str,
    operations: Sequence[BenchmarkQuery],
    *,
    warmups: int,
    samples: int,
) -> list[StoreBenchmarkResult]:
    """Measure frozen operations through production PostgreSQL methods.

    Time complexity is O((warmups + samples) * Q + J), where Q is the
    operations and J is the bounded scan lookup seed count.

    Args:
        dsn: PostgreSQL DSN for the quiescent rehearsal database.
        operations: Validated backend-neutral operation descriptors.
        warmups: Unrecorded executions per operation.
        samples: Recorded executions per operation.

    Returns:
        Results in descriptor order.

    Raises:
        ValueError: If a required benchmark seed or operation returns no rows.
    """
    store = PostgresStore(dsn, min_size=1, max_size=2)
    await store.connect()
    try:
        seeds = await _seed_inputs(store, limit=100)
        detail = JobsViewService(store, HardFilters())
        reports = []
        for query in operations:
            call = _operation_call(store, detail, query, seeds)
            durations = []
            row_count = 0
            for index in range(warmups + samples):
                started = time.perf_counter_ns()
                result = await call()
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                row_count = _count_rows(result)
                if row_count == 0:
                    raise ValueError(
                        f"benchmark operation {query.operation} returned zero rows"
                    )
                if index >= warmups:
                    durations.append(elapsed_ms)
            reports.append(
                StoreBenchmarkResult(samples_ms=durations, row_count=row_count)
            )
        return reports
    finally:
        await store.close()


async def capture_postgres_store_aggregates(dsn: str) -> dict[str, object]:
    """Capture exact store outputs used as cross-backend aggregate goldens.

    Args:
        dsn: PostgreSQL DSN for the quiescent rehearsal database.

    Returns:
        Pending counts and raw view/performance result objects.
    """
    store = PostgresStore(dsn, min_size=1, max_size=2)
    await store.connect()
    try:
        pending_stage_a = await store.load_pending_stage_a(limit=1_000_000)
        pending_stage_b = await store.load_pending_stage_b(limit=1_000_000)
        return {
            "pending_stage_a": len(pending_stage_a),
            "pending_stage_b": len(pending_stage_b),
            "needs_attention": await store.needs_attention(
                days=30, max_per_category=100_000
            ),
            "funnel": await store.get_funnel_stats(30),
            "daily_cost": await store.get_cost_range(since_days=30),
            "llm_percentiles": await store.get_llm_daily_stats(30),
        }
    finally:
        await store.close()
