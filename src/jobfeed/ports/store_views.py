"""Jobs-view store port: the bounded, filtered web read path."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import PipelineRun
from jobfeed.domain.models_views import (
    InsightsOverview,
    JobsViewPage,
    JobsViewQuery,
    JobsViewRow,
    TwinStatusRow,
)


@runtime_checkable
class StoreViewsMixin(Protocol):
    """Read-only jobs view for the web API."""

    async def query_jobs_view(self, query: JobsViewQuery) -> JobsViewPage:
        """Run the filtered, paginated jobs view query.

        Args:
            query: Tab, filters, sort, and pagination window.

        Returns:
            Bounded page: the active tab's rows ordered per ``query.sort``
            (default newest discovered first), the active tab's full match
            count, and per-tab counts under the same request filters.

        Note:
            ``limit``/``offset`` window the SQL prefilter, before any
            in-memory hard filter or display fold. Callers that fold or
            hard-filter post-query (triage tabs, plan D10) must request the
            full corpus (large limit) and paginate post-fold, or the fold
            corpus is silently truncated.
        """
        ...

    async def list_twin_rows_by_status(
        self,
        keys: Sequence[tuple[str, str]],
        *,
        statuses: Sequence[str],
        limit: int,
    ) -> list[JobsViewRow]:
        """List view rows in the given statuses for the given twin keys.

        Feeds the display fold (plan D9): a tab-filtered corpus never
        contains out-of-tab siblings, so the fold pulls them in explicitly
        (e.g. the in-flight ``applied`` twin of a queue row).

        Args:
            keys: Non-blank ``(company_norm, title_norm)`` cluster keys.
            statuses: Workflow statuses to keep.
            limit: Maximum rows returned.

        Returns:
            Matching view rows, newest discovered first. Blank-norm rows
            never match (mirroring ``list_twin_statuses``).
        """
        ...

    async def list_twin_statuses(self, job_id: str) -> list[TwinStatusRow]:
        """List a job's twins (same persisted company_norm + title_norm).

        The job itself is excluded. A job whose ``company_norm`` OR
        ``title_norm`` is blank/NULL has no twins (mirroring
        ``expand_twin_ids``: blank-norm rows never cluster).

        Args:
            job_id: Store-assigned identity of the detail job.

        Returns:
            Twin rows (platform, url, current status), ordered by job id.
        """
        ...

    async def list_pipeline_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PipelineRun], int]:
        """List pipeline runs, newest first, with the all-time total.

        Ordered by ``started_at`` descending with ``run_id`` descending as a
        deterministic tiebreak.

        Args:
            limit: Maximum runs returned.
            offset: Runs to skip before the returned window.

        Returns:
            Tuple of (runs window, total run count ignoring the window).
        """
        ...

    async def insights_overview(self, *, window_days: int) -> InsightsOverview:
        """Aggregate the insights overview.

        Totals and the verdict/status distributions are all-time; only the
        daily series is windowed (UTC day buckets over
        ``[now - window_days, now]``, emitting only days having data).

        Args:
            window_days: Daily-series window in days (caller-validated).

        Returns:
            Insights overview aggregate.
        """
        ...
