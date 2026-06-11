"""Jobs-view store port: the bounded, filtered web read path."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models_views import JobsViewPage, JobsViewQuery


@runtime_checkable
class StoreViewsMixin(Protocol):
    """Read-only jobs view for the web API."""

    async def query_jobs_view(self, query: JobsViewQuery) -> JobsViewPage:
        """Run the filtered, paginated jobs view query.

        Args:
            query: Tab, filters, and pagination window.

        Returns:
            Bounded page: the active tab's rows (newest discovered first),
            the active tab's full match count, and per-tab counts under the
            same request filters.

        Note:
            ``limit``/``offset`` window the SQL prefilter, before any
            in-memory hard filter or display fold. Callers that fold or
            hard-filter post-query (triage tabs, plan D10) must request the
            full corpus (large limit) and paginate post-fold, or the fold
            corpus is silently truncated.
        """
        ...
