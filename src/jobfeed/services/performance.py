"""Performance metrics service: thin orchestrator over store queries.

Delegates all query work to the ``StorePerfMixin`` port. Exists so that
web routes depend on a service (not the store directly), keeping the
hexagonal boundary intact and leaving room for caching or aggregation
logic later.
"""

from __future__ import annotations

from jobfeed.domain.models_perf import (
    FunnelStats,
    LLMDailyStats,
    PerformanceOverview,
    StepTimingSeries,
)
from jobfeed.ports.store_perf import StorePerfMixin


class PerformanceService:
    """Aggregates pipeline performance metrics from the store."""

    def __init__(self, store: StorePerfMixin) -> None:
        """Create the service with an injected store.

        Args:
            store: Persistence port with the performance query capability.
        """
        self._store = store

    async def get_overview(self, window: int) -> PerformanceOverview:
        """Aggregate overview metrics with period-over-period deltas.

        Args:
            window: Window in days.

        Returns:
            Performance overview.
        """
        return await self._store.get_performance_overview(window)

    async def get_step_timings(
        self, window: int, step_type: str | None = None
    ) -> list[StepTimingSeries]:
        """Fetch step timing rows within the window.

        Args:
            window: Window in days.
            step_type: Optional step type filter.

        Returns:
            Step timing series.
        """
        return await self._store.get_step_timings(window, step_type)

    async def get_llm_stats(self, window: int) -> list[LLMDailyStats]:
        """Daily LLM latency percentiles and token averages.

        Args:
            window: Window in days.

        Returns:
            Daily LLM stats.
        """
        return await self._store.get_llm_daily_stats(window)

    async def get_funnel_stats(self, window: int) -> list[FunnelStats]:
        """Evaluation funnel snapshots from pipeline runs.

        Args:
            window: Window in days.

        Returns:
            Funnel stats per run.
        """
        return await self._store.get_funnel_stats(window)


__all__ = ["PerformanceService"]
