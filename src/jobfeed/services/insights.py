"""Insights service: composes the store overview with application stats.

The store aggregate (totals, distributions, daily series) and the windowed
``ApplicationService.stats(by_resume=True)`` table are independent reads;
this service only bundles them for the web overview endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import ApplicationStats
from jobfeed.domain.models_views import InsightsOverview
from jobfeed.services.application import ApplicationService


@runtime_checkable
class InsightsStore(Protocol):
    """Store capability required by InsightsService."""

    async def insights_overview(self, *, window_days: int) -> InsightsOverview:
        """Aggregate the insights overview.

        Totals and the verdict/status distributions are all-time; only the
        daily series is windowed (UTC day buckets, days having data only).

        Args:
            window_days: Daily-series window in days (caller-validated).

        Returns:
            Insights overview aggregate.
        """
        ...


@dataclass(kw_only=True)
class InsightsBundle:
    """Overview aggregate plus the windowed application stats table."""

    overview: InsightsOverview
    applications: ApplicationStats


class InsightsService:
    """Composes the insights overview with the by-resume application stats."""

    def __init__(self, store: InsightsStore, applications: ApplicationService) -> None:
        """Create the service with injected dependencies.

        Args:
            store: Persistence port with the insights aggregate capability.
            applications: Application service for the by-resume stats table.
        """
        self._store = store
        self._applications = applications

    async def overview(self, *, window_days: int) -> InsightsBundle:
        """Compose the full insights overview for one request.

        Args:
            window_days: Window in days, shared by the store's daily series
                and the application stats look-back.

        Returns:
            Store aggregate plus application stats with the by-resume
            breakdown.
        """
        overview = await self._store.insights_overview(window_days=window_days)
        applications = await self._applications.stats(
            since_days_ago=window_days, by_resume=True
        )
        return InsightsBundle(overview=overview, applications=applications)


__all__ = ["InsightsBundle", "InsightsService", "InsightsStore"]
