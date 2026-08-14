"""SQLite timing persistence and performance dashboard aggregates."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from statistics import mean

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_rows,
    _immediate_transaction,
    _parse_utc_timestamp,
    _require_utc_timestamp,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models_perf import (
    FunnelStats,
    LLMDailyStats,
    PerformanceOverview,
    StepTiming,
    StepTimingSeries,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def zero_overview() -> PerformanceOverview:
    """Return the frozen empty performance aggregate.

    Returns:
        Zero-valued metrics and absent previous-window deltas.
    """
    return PerformanceOverview(
        avg_scan_duration_ms=0,
        avg_eval_duration_ms=0,
        total_llm_cost_usd=0,
        error_rate=0,
        scan_duration_delta=None,
        eval_duration_delta=None,
        cost_delta=None,
        error_rate_delta=None,
    )


def _delta(current: float, previous: float, previous_count: int) -> float | None:
    if previous_count == 0 or previous <= 0:
        return None
    return (current - previous) / previous


def _run_metrics(rows: Sequence[aiosqlite.Row]) -> tuple[float, float, float, float]:
    scan: list[float] = []
    evaluate: list[float] = []
    cost = 0.0
    errors = 0
    for row in rows:
        started = _parse_utc_timestamp(row["started_at"])
        finished = _parse_utc_timestamp(row["finished_at"])
        elapsed = (finished - started).total_seconds() * 1000
        source = str(row["source"])
        status = str(row["status"])
        is_evaluate = "evaluat" in source.casefold()
        if status == "succeeded" and not is_evaluate:
            scan.append(elapsed)
        if status == "succeeded" and is_evaluate:
            evaluate.append(elapsed)
        cost += float(row["total_llm_cost_usd"])
        errors += int(int(row["errors"]) > 0 or status == "failed")
    return (
        mean(scan) if scan else 0.0,
        mean(evaluate) if evaluate else 0.0,
        cost,
        errors / len(rows) if rows else 0.0,
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


class _SqlitePerformance:
    """Internal mixin implementing the typed performance port."""

    _lifecycle: SqliteLifecycle

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Persist one timing using the schema-owned creation timestamp."""
        async with self._lifecycle.connection() as connection:
            await connection.execute(
                """INSERT INTO step_timings
                    (run_id, step_type, step_name, elapsed_ms, is_error)
                    VALUES (?, ?, ?, ?, ?)""",
                (
                    timing.run_id,
                    timing.step_type,
                    timing.step_name,
                    timing.elapsed_ms,
                    int(timing.is_error),
                ),
            )

    async def record_step_timings(self, timings: list[StepTiming]) -> None:
        """Persist a timing batch atomically and preserve its input order."""
        if not timings:
            return
        values = [
            (
                timing.run_id,
                timing.step_type,
                timing.step_name,
                timing.elapsed_ms,
                int(timing.is_error),
            )
            for timing in timings
        ]
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            await connection.executemany(
                """INSERT INTO step_timings
                    (run_id, step_type, step_name, elapsed_ms, is_error)
                    VALUES (?, ?, ?, ?, ?)""",
                values,
            )

    async def get_performance_overview(self, window_days: int) -> PerformanceOverview:
        """Return completed-run aggregates for current and previous windows."""
        now = _utc_now()
        current_cutoff = now - timedelta(days=window_days)
        previous_cutoff = now - timedelta(days=window_days * 2)
        async with self._lifecycle.connection() as connection:
            current = await _fetch_rows(
                connection,
                """SELECT * FROM pipeline_runs
                   WHERE started_at>=?
                     AND finished_at IS NOT NULL""",
                (_require_utc_timestamp(current_cutoff),),
            )
            previous = await _fetch_rows(
                connection,
                """SELECT * FROM pipeline_runs
                   WHERE started_at>=? AND started_at<?
                     AND finished_at IS NOT NULL""",
                (
                    _require_utc_timestamp(previous_cutoff),
                    _require_utc_timestamp(current_cutoff),
                ),
            )
        cur_scan, cur_eval, cur_cost, cur_error = _run_metrics(current)
        prev_scan, prev_eval, prev_cost, prev_error = _run_metrics(previous)
        return PerformanceOverview(
            avg_scan_duration_ms=cur_scan,
            avg_eval_duration_ms=cur_eval,
            total_llm_cost_usd=cur_cost,
            error_rate=cur_error,
            scan_duration_delta=_delta(cur_scan, prev_scan, len(previous)),
            eval_duration_delta=_delta(cur_eval, prev_eval, len(previous)),
            cost_delta=_delta(cur_cost, prev_cost, len(previous)),
            error_rate_delta=_delta(cur_error, prev_error, len(previous)),
        )

    async def get_step_timings(
        self, window_days: int, step_type: str | None = None
    ) -> list[StepTimingSeries]:
        """Return inclusive-window timing rows ordered by timestamp and ID."""
        now = _utc_now()
        clauses = ["created_at>=?"]
        params: list[object] = [
            _require_utc_timestamp(now - timedelta(days=window_days))
        ]
        if step_type is not None:
            clauses.append("step_type=?")
            params.append(step_type)
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                """SELECT step_type, step_name, run_id, elapsed_ms,
                          is_error, created_at FROM step_timings
                   WHERE """
                + " AND ".join(clauses)
                + " ORDER BY created_at, id",
                params,
            )
        return [
            StepTimingSeries(
                step_type=str(row["step_type"]),
                step_name=str(row["step_name"]),
                run_id=str(row["run_id"]),
                elapsed_ms=float(row["elapsed_ms"]),
                is_error=bool(row["is_error"]),
                created_at=_parse_utc_timestamp(row["created_at"]),
            )
            for row in rows
        ]

    async def get_llm_daily_stats(self, window_days: int) -> list[LLMDailyStats]:
        """Return UTC daily continuous percentiles and token averages."""
        now = _utc_now()
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                """SELECT substr(timestamp, 1, 10) AS day, model, stage,
                          latency_ms, input_tokens, output_tokens
                   FROM llm_usage
                   WHERE timestamp>=?
                   ORDER BY day, model, stage, latency_ms, id""",
                (_require_utc_timestamp(now - timedelta(days=window_days)),),
            )
        by_day: dict[tuple[str, str, str | None], list[aiosqlite.Row]] = {}
        for row in rows:
            key = (str(row["day"]), str(row["model"]), row["stage"])
            by_day.setdefault(key, []).append(row)
        return [
            LLMDailyStats(
                day=day,
                model=model,
                stage=stage,
                p50_latency_ms=_percentile(
                    [float(row["latency_ms"]) for row in records], 0.5
                ),
                p95_latency_ms=_percentile(
                    [float(row["latency_ms"]) for row in records], 0.95
                ),
                call_count=len(records),
                avg_input_tokens=mean(float(row["input_tokens"]) for row in records),
                avg_output_tokens=mean(float(row["output_tokens"]) for row in records),
            )
            for (day, model, stage), records in by_day.items()
        ]

    async def get_funnel_stats(self, window_days: int) -> list[FunnelStats]:
        """Return exact evaluate-run funnel snapshots, newest first."""
        now = _utc_now()
        async with self._lifecycle.connection() as connection:
            rows = await _fetch_rows(
                connection,
                """SELECT run_id, jobs_filtered, jobs_ml_gated,
                          jobs_gate_passed, stage_a_scored, stage_b_scored
                   FROM pipeline_runs
                   WHERE started_at>=? AND source='evaluate'
                   ORDER BY started_at DESC""",
                (_require_utc_timestamp(now - timedelta(days=window_days)),),
            )
        result: list[FunnelStats] = []
        for row in rows:
            after_gate = max(
                int(row["jobs_gate_passed"]),
                int(row["stage_a_scored"]),
                int(row["stage_b_scored"]),
            )
            scored = max(int(row["stage_a_scored"]), int(row["stage_b_scored"]))
            after_filter = int(row["jobs_ml_gated"]) + after_gate
            result.append(
                FunnelStats(
                    run_id=str(row["run_id"]),
                    total_candidates=int(row["jobs_filtered"]) + after_filter,
                    after_filter=after_filter,
                    after_gate=after_gate,
                    scored=scored,
                )
            )
        return result
