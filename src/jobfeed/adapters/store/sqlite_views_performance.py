"""Composed SQLite views and performance capability."""

from __future__ import annotations

from jobfeed.adapters.store._sqlite_insights import _SqliteInsights
from jobfeed.adapters.store._sqlite_performance import _SqlitePerformance
from jobfeed.adapters.store._sqlite_views import _SqliteViews
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class SqliteViewsPerformance(_SqliteViews, _SqliteInsights, _SqlitePerformance):
    """Provide SQLite views and performance operations over one lifecycle."""

    def __init__(self, lifecycle: SqliteLifecycle) -> None:
        """Bind every operation to the caller-owned shared lifecycle."""
        self._lifecycle = lifecycle


__all__ = ["SqliteViewsPerformance"]
