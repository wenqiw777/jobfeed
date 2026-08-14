"""Insights service for the selected discovery-period overview."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models_views import InsightsOverview


@runtime_checkable
class InsightsStore(Protocol):
    """Store capability required by InsightsService."""

    async def insights_overview(self, *, window_days: int | None) -> InsightsOverview:
        """Aggregate the insights overview.

        The window selects the discovery-date cohort for totals,
        distributions, and daily UTC buckets (days having data only).

        Args:
            window_days: Daily-series window in days (caller-validated), or
                None for all time.

        Returns:
            Insights overview aggregate.
        """
        ...


class InsightsService:
    """Returns the selected discovery-period overview."""

    def __init__(self, store: InsightsStore) -> None:
        """Create the service with injected dependencies.

        Args:
            store: Persistence port with the insights aggregate capability.
        """
        self._store = store

    async def overview(self, *, window_days: int | None) -> InsightsOverview:
        """Return the insights overview for one request.

        Args:
            window_days: Discovery window in days, or None for all time.

        Returns:
            Selected-period store aggregate.
        """
        return await self._store.insights_overview(window_days=window_days)


__all__ = ["InsightsService", "InsightsStore"]
