"""Jobs view service: list composition and detail aggregation for the web API.

List composition order (plan D10): bounded SQL prefilter via
``query_jobs_view`` -> optional hard filters -> optional display fold (D9)
-> sort -> in-memory pagination with true totals. Triage tabs (queue,
pending_jd) and any post-processed request over-fetch the corpus from the
store (``JOBS_VIEW_CORPUS_LIMIT``) because SQL pagination would window rows
BEFORE the in-memory drop/fold steps; Library tabs with the default sort and
no post-processing flags pass limit/offset straight to SQL. ``tab_counts``
pass through from the store — SQL-prefilter counts, never post-fold counts.

The pure sort keys live in ``services/_jobs_view_sort.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from jobfeed.domain import filtering
from jobfeed.domain.dedupe import pick_display_representatives
from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models import (
    ApplicationRecord,
    JobEvaluation,
    JobPosting,
    StatusInfo,
)
from jobfeed.domain.models_views import (
    JobsViewPage,
    JobsViewQuery,
    JobsViewRow,
    TwinStatusRow,
)
from jobfeed.ports.store_views import StoreViewsMixin
from jobfeed.services._jobs_view_sort import (
    DEFAULT_SORT,
    LIBRARY_SORT_KEYS,
    VALID_SORTS,
    verdict_group_sort_key,
)

#: Over-fetch bound for post-processed list requests (plan D10). Triage
#: corpora are 10^2-scale; the cap keeps the in-memory pipeline bounded even
#: against a pathological database.
JOBS_VIEW_CORPUS_LIMIT = 10_000

_TRIAGE_TABS = frozenset({"queue", "pending_jd"})


@runtime_checkable
class JobsViewStore(StoreViewsMixin, Protocol):
    """Store capability required by JobsViewService (views + detail reads)."""

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load one job by store identity.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Job posting if found, else None.
        """
        ...

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Load a job's evaluation (Stage A/B optional).

        Args:
            job_id: Store-assigned identity.

        Returns:
            Evaluation if the job exists, else None.
        """
        ...

    async def get_status(self, job_id: str) -> StatusInfo | None:
        """Load a job's current status info.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Status info if found, else None.
        """
        ...

    async def get_status_history(self, job_id: str) -> list[str]:
        """Load a job's status history.

        Args:
            job_id: Store-assigned identity.

        Returns:
            to_status history values, newest first.
        """
        ...

    async def list_interview_rounds(self, job_id: str) -> list[InterviewRound]:
        """List a job's interview rounds.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Interview rounds, ascending by round_index.
        """
        ...

    async def get_application(self, job_id: str) -> ApplicationRecord | None:
        """Load a job's application audit record.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Application record, or None when never applied.
        """
        ...


@dataclass(kw_only=True)
class JobDetail:
    """Aggregated detail for one job: evaluation, workflow, twins, audit."""

    job: JobPosting
    evaluation: JobEvaluation | None
    status: StatusInfo | None
    history: list[str]
    twins: list[TwinStatusRow]
    interviews: list[InterviewRound]
    application: ApplicationRecord | None


class JobsViewService:
    """Composes the jobs list view and the per-job detail aggregate."""

    def __init__(
        self, store: JobsViewStore, hard_filters: filtering.HardFilters
    ) -> None:
        """Create the service with an injected store and hard-filter config.

        Args:
            store: Persistence port with views + detail read capabilities.
            hard_filters: Settings-derived filters applied on opt-in.
        """
        self._store = store
        self._hard_filters = hard_filters

    async def list_jobs(
        self,
        query: JobsViewQuery,
        *,
        apply_hard_filters: bool = False,
        dedupe: bool = False,
        sort: str = DEFAULT_SORT,
    ) -> JobsViewPage:
        """Run the composed list view for one request.

        Triage tabs always use the fixed verdict-group order (``sort`` is
        ignored there per plan A4); Library tabs honor ``sort``. Requests
        needing post-processing over-fetch and paginate in memory with the
        true post-processing total; plain Library requests paginate in SQL.

        Args:
            query: Tab, filters, and the requested pagination window.
            apply_hard_filters: Drop rows failing the configured hard filters.
            dedupe: Fold twin clusters to one display representative (D9).
            sort: Library sort name (one of ``VALID_SORTS``).

        Returns:
            Page with the requested window, true total, and SQL tab counts.

        Raises:
            ValueError: If ``sort`` is not a known sort name.
        """
        if sort not in VALID_SORTS:
            raise ValueError(f"unknown jobs view sort: {sort!r}")
        is_triage = query.tab in _TRIAGE_TABS
        effective_sort = DEFAULT_SORT if is_triage else sort
        needs_post_processing = (
            is_triage or apply_hard_filters or dedupe or effective_sort != DEFAULT_SORT
        )
        if not needs_post_processing:
            return await self._store.query_jobs_view(query)
        corpus = await self._store.query_jobs_view(
            replace(query, limit=JOBS_VIEW_CORPUS_LIMIT, offset=0)
        )
        rows = corpus.rows
        if apply_hard_filters:
            rows = self._drop_hard_filtered(rows)
        if dedupe:
            rows = _fold_to_display_representatives(rows)
        if is_triage:
            rows = sorted(rows, key=verdict_group_sort_key)
        else:
            rows = sorted(rows, key=LIBRARY_SORT_KEYS[effective_sort])
        return JobsViewPage(
            rows=rows[query.offset : query.offset + query.limit],
            # Beyond the corpus cap, len(rows) (reachable rows) is deliberately
            # preferred over corpus.total to avoid advertising phantom pages.
            total=len(rows),
            tab_counts=corpus.tab_counts,
        )

    async def get_job_detail(self, job_id: str) -> JobDetail | None:
        """Aggregate the full detail view for one job.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Aggregated detail, or None when the job does not exist.
        """
        job = await self._store.get_job(job_id)
        if job is None:
            return None
        return JobDetail(
            job=job,
            evaluation=await self._store.get_evaluation(job_id),
            status=await self._store.get_status(job_id),
            history=await self._store.get_status_history(job_id),
            twins=await self._store.list_twin_statuses(job_id),
            interviews=await self._store.list_interview_rounds(job_id),
            application=await self._store.get_application(job_id),
        )

    def _drop_hard_filtered(self, rows: list[JobsViewRow]) -> list[JobsViewRow]:
        """Keep only rows whose job passes the configured hard filters."""
        return [
            row
            for row in rows
            if filtering.apply_hard_filters(row.job, self._hard_filters) is None
        ]


def _fold_to_display_representatives(rows: list[JobsViewRow]) -> list[JobsViewRow]:
    """Fold twin clusters to one status-aware display representative each.

    Delegates clustering and selection to the pure domain fold (plan D9) over
    the rows' jobs, then maps the winning jobs back to their rows via an
    id-keyed dict. Time complexity: O(n) over the corpus.

    Args:
        rows: View rows to fold (store rows always carry a job id).

    Returns:
        One row per twin cluster, in cluster (first-seen) order.
    """
    row_by_id = {row.job.id: row for row in rows if row.job.id is not None}
    representatives = pick_display_representatives(
        [row.job for row in rows],
        {job_id: row.status for job_id, row in row_by_id.items()},
    )
    return [row_by_id[job.id] for job in representatives if job.id is not None]


__all__ = [
    "JOBS_VIEW_CORPUS_LIMIT",
    "VALID_SORTS",
    "JobDetail",
    "JobsViewService",
    "JobsViewStore",
]
