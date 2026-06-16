"""Performance observation store port.

Records step-level timing data for pipeline runs and exposes query
methods for the performance dashboard: overview aggregates, step timing
series, LLM daily stats, and evaluation funnel snapshots.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models_perf import (
    FunnelStats,
    LLMDailyStats,
    PerformanceOverview,
    StepTiming,
    StepTimingSeries,
)


@runtime_checkable
class StorePerfMixin(Protocol):
    """Step-timing persistence and performance query capability."""

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Persist a single step timing record.

        Args:
            timing: Step timing to persist.
        """
        ...

    async def record_step_timings(self, timings: list[StepTiming]) -> None:
        """Persist multiple step timing records in a single batch.

        Args:
            timings: Step timings to persist.
        """
        ...

    async def get_performance_overview(self, window_days: int) -> PerformanceOverview:
        """Aggregate performance metrics over a window with deltas.

        Args:
            window_days: Number of days in the current window.

        Returns:
            Overview with averages, totals, and period-over-period deltas.
        """
        ...

    async def get_step_timings(
        self, window_days: int, step_type: str | None = None
    ) -> list[StepTimingSeries]:
        """Fetch step timing rows within the window.

        Args:
            window_days: Look-back window in days.
            step_type: Optional filter on step_type.

        Returns:
            Step timings ordered by created_at ascending.
        """
        ...

    async def get_llm_daily_stats(self, window_days: int) -> list[LLMDailyStats]:
        """Daily LLM latency percentiles and token averages.

        Args:
            window_days: Look-back window in days.

        Returns:
            Daily stats ordered by day ascending.
        """
        ...

    async def get_funnel_stats(self, window_days: int) -> list[FunnelStats]:
        """Evaluation funnel snapshots from pipeline runs.

        Args:
            window_days: Look-back window in days.

        Returns:
            Funnel stats per run, newest first.
        """
        ...
