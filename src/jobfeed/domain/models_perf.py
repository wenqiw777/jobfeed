"""Performance observation domain dataclasses.

Step-level timing, aggregated overview, LLM daily stats, and evaluation
funnel snapshots used by the performance dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(kw_only=True)
class StepTiming:
    """Elapsed wall-clock time for one pipeline step execution."""

    run_id: str
    step_type: str
    step_name: str
    elapsed_ms: float
    is_error: bool = False
    created_at: datetime | None = None


@dataclass(kw_only=True)
class PerformanceOverview:
    """Aggregated performance metrics over a window with deltas."""

    avg_scan_duration_ms: float
    avg_eval_duration_ms: float
    total_llm_cost_usd: float
    error_rate: float
    scan_duration_delta: float | None
    eval_duration_delta: float | None
    cost_delta: float | None
    error_rate_delta: float | None


@dataclass(kw_only=True)
class StepTimingSeries:
    """One step timing row for the performance time-series chart."""

    step_type: str
    step_name: str
    run_id: str
    elapsed_ms: float
    is_error: bool
    created_at: datetime


@dataclass(kw_only=True)
class LLMDailyStats:
    """Per-day, per-model LLM latency and token aggregates."""

    day: str
    model: str
    stage: str | None
    p50_latency_ms: float
    p95_latency_ms: float
    call_count: int
    avg_input_tokens: float
    avg_output_tokens: float


@dataclass(kw_only=True)
class FunnelStats:
    """Evaluation funnel counts for one pipeline run."""

    run_id: str
    total_candidates: int
    after_filter: int
    after_gate: int
    scored: int
