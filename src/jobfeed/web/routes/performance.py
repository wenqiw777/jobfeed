"""Performance dashboard routes (read-only).

All routes delegate to ``PerformanceService``, never touching the store
directly. Query parameters follow the same ``window`` convention as the
insights routes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from jobfeed.services.performance import PerformanceService
from jobfeed.web.deps import get_performance_service

_DEFAULT_WINDOW_DAYS = 30
_MAX_WINDOW_DAYS = 365

router = APIRouter()

_Perf = Annotated[PerformanceService, Depends(get_performance_service)]


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PerformanceOverviewResponse(BaseModel):
    """``GET /performance/overview`` response."""

    avg_scan_duration_ms: float
    avg_eval_duration_ms: float
    total_llm_cost_usd: float
    error_rate: float
    scan_duration_delta: float | None
    eval_duration_delta: float | None
    cost_delta: float | None
    error_rate_delta: float | None


class StepTimingRow(BaseModel):
    """One step timing row for the series endpoint."""

    step_type: str
    step_name: str
    run_id: str
    elapsed_ms: float
    is_error: bool
    created_at: datetime


class StepTimingsResponse(BaseModel):
    """``GET /performance/step-timings`` response."""

    timings: list[StepTimingRow]


class LLMDailyStatsRow(BaseModel):
    """One day of LLM stats."""

    day: str
    p50_latency_ms: float
    p95_latency_ms: float
    avg_input_tokens: float
    avg_output_tokens: float


class LLMStatsResponse(BaseModel):
    """``GET /performance/llm-stats`` response."""

    stats: list[LLMDailyStatsRow]


class FunnelRow(BaseModel):
    """One funnel snapshot for a pipeline run."""

    run_id: str
    total_candidates: int
    after_filter: int
    after_gate: int
    scored: int


class FunnelResponse(BaseModel):
    """``GET /performance/funnel`` response."""

    funnel: list[FunnelRow]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/performance/overview")
async def performance_overview(
    service: _Perf,
    window: Annotated[int, Query(ge=1, le=_MAX_WINDOW_DAYS)] = _DEFAULT_WINDOW_DAYS,
) -> PerformanceOverviewResponse:
    """Aggregate performance metrics with period-over-period deltas.

    Args:
        service: Shared performance service.
        window: Window in days (1..365).

    Returns:
        Performance overview response.
    """
    overview = await service.get_overview(window)
    return PerformanceOverviewResponse(
        avg_scan_duration_ms=overview.avg_scan_duration_ms,
        avg_eval_duration_ms=overview.avg_eval_duration_ms,
        total_llm_cost_usd=overview.total_llm_cost_usd,
        error_rate=overview.error_rate,
        scan_duration_delta=overview.scan_duration_delta,
        eval_duration_delta=overview.eval_duration_delta,
        cost_delta=overview.cost_delta,
        error_rate_delta=overview.error_rate_delta,
    )


@router.get("/performance/step-timings")
async def step_timings(
    service: _Perf,
    window: Annotated[int, Query(ge=1, le=_MAX_WINDOW_DAYS)] = _DEFAULT_WINDOW_DAYS,
    step_type: Annotated[str | None, Query()] = None,
) -> StepTimingsResponse:
    """Step timing series within the window, optionally filtered.

    Args:
        service: Shared performance service.
        window: Window in days (1..365).
        step_type: Optional filter on step_type.

    Returns:
        Step timings response.
    """
    rows = await service.get_step_timings(window, step_type)
    return StepTimingsResponse(
        timings=[
            StepTimingRow(
                step_type=r.step_type,
                step_name=r.step_name,
                run_id=r.run_id,
                elapsed_ms=r.elapsed_ms,
                is_error=r.is_error,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


@router.get("/performance/llm-stats")
async def llm_stats(
    service: _Perf,
    window: Annotated[int, Query(ge=1, le=_MAX_WINDOW_DAYS)] = _DEFAULT_WINDOW_DAYS,
) -> LLMStatsResponse:
    """Daily LLM latency percentiles and token averages.

    Args:
        service: Shared performance service.
        window: Window in days (1..365).

    Returns:
        LLM daily stats response.
    """
    rows = await service.get_llm_stats(window)
    return LLMStatsResponse(
        stats=[
            LLMDailyStatsRow(
                day=r.day,
                p50_latency_ms=r.p50_latency_ms,
                p95_latency_ms=r.p95_latency_ms,
                avg_input_tokens=r.avg_input_tokens,
                avg_output_tokens=r.avg_output_tokens,
            )
            for r in rows
        ]
    )


@router.get("/performance/funnel")
async def funnel(
    service: _Perf,
    window: Annotated[int, Query(ge=1, le=_MAX_WINDOW_DAYS)] = _DEFAULT_WINDOW_DAYS,
) -> FunnelResponse:
    """Evaluation funnel snapshots from pipeline runs.

    Args:
        service: Shared performance service.
        window: Window in days (1..365).

    Returns:
        Funnel response.
    """
    rows = await service.get_funnel_stats(window)
    return FunnelResponse(
        funnel=[
            FunnelRow(
                run_id=r.run_id,
                total_candidates=r.total_candidates,
                after_filter=r.after_filter,
                after_gate=r.after_gate,
                scored=r.scored,
            )
            for r in rows
        ]
    )


__all__ = ["router"]
