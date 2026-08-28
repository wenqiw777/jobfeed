"""Jobs view service: list composition and detail aggregation for the web API.

List composition order (plan D10): bounded SQL prefilter via
``query_jobs_view`` -> optional hard filters -> optional display fold (D9)
-> sort -> in-memory pagination with true totals. Triage tabs (queue,
pending_jd) and any post-processed request over-fetch the corpus from the
store (``JOBS_VIEW_CORPUS_LIMIT``) because SQL pagination would window rows
BEFORE the in-memory drop/fold steps; Library tabs with no post-processing
flags pass sort/limit/offset straight to SQL (any sort — the store orders
per ``query.sort``). ``tab_counts`` pass through from the store —
SQL-prefilter counts, never post-fold counts.

The display fold pulls in-flight (applied/interviewing/offer) twins of the
corpus rows into the fold input even when the tab excludes them, so a
posting applied on one platform suppresses its still-in-queue siblings (D9).

The pure sort keys live in ``services/_jobs_view_sort.py``; the fold step
lives in ``services/_jobs_view_fold.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from jobfeed.domain import filtering
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
from jobfeed.services._jobs_view_fold import fold_with_inflight_twins
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
_FAST_PAGE_MULTIPLIER = 5

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
        fast: bool = False,
    ) -> JobsViewPage:
        """Run the composed list view for one request.

        Triage tabs retain verdict-group order for the default discovered
        sort and honor an explicit alternative sort. Library tabs honor
        ``sort``. Requests
        needing post-processing over-fetch and paginate in memory with the
        true post-processing total; plain Library requests paginate in SQL
        for ANY sort (the store orders per ``query.sort``).

        Args:
            query: Tab, filters, and the requested pagination window.
            apply_hard_filters: Drop rows failing the configured hard filters.
            dedupe: Fold twin clusters to one display representative (D9).
            sort: Sort name (one of ``VALID_SORTS``).

        Returns:
            Page with the requested window, true total, and SQL tab counts.

        Raises:
            ValueError: If ``sort`` is not a known sort name.
        """
        if sort not in VALID_SORTS:
            raise ValueError(f"unknown jobs view sort: {sort!r}")
        is_triage = query.tab in _TRIAGE_TABS
        effective_sort = sort
        if not (is_triage or apply_hard_filters or dedupe):
            return await self._store.query_jobs_view(
                replace(query, sort=effective_sort)
            )
        if fast and query.offset == 0:
            candidate_limit = min(
                JOBS_VIEW_CORPUS_LIMIT,
                max(query.limit, query.limit * _FAST_PAGE_MULTIPLIER),
            )
            corpus = await self._store.query_jobs_view(
                replace(
                    query,
                    limit=candidate_limit,
                    offset=0,
                    # The provisional window reads only the evaluated corpus;
                    # the exact background request applies the chosen global
                    # sort across every matching row.
                    sort=DEFAULT_SORT,
                    include_counts=False,
                )
            )
            rows = corpus.rows
            if apply_hard_filters:
                rows = self._drop_hard_filtered(rows)
            # Exact cross-status dedupe is deliberately deferred to the
            # background request; its twin lookup would block first paint.
            sort_key = (
                verdict_group_sort_key
                if is_triage and effective_sort == DEFAULT_SORT
                else LIBRARY_SORT_KEYS[effective_sort]
            )
            rows = sorted(rows, key=sort_key)
            return JobsViewPage(
                rows=rows[: query.limit],
                total=len(rows),
                tab_counts={},
                total_is_exact=False,
            )
        # Over-fetch corpus in the effective SQL order so the cap keeps the
        # best rows under the requested sort even on pathological databases.
        corpus = await self._store.query_jobs_view(
            replace(
                query,
                limit=JOBS_VIEW_CORPUS_LIMIT,
                offset=0,
                sort=effective_sort,
                include_total=False,
            )
        )
        rows = corpus.rows
        if apply_hard_filters:
            rows = self._drop_hard_filtered(rows)
        if dedupe:
            rows = await fold_with_inflight_twins(
                self._store, rows, twin_limit=JOBS_VIEW_CORPUS_LIMIT
            )
        sort_key = (
            verdict_group_sort_key
            if is_triage and effective_sort == DEFAULT_SORT
            else LIBRARY_SORT_KEYS[effective_sort]
        )
        rows = sorted(rows, key=sort_key)
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


__all__ = [
    "JOBS_VIEW_CORPUS_LIMIT",
    "VALID_SORTS",
    "JobDetail",
    "JobsViewService",
    "JobsViewStore",
]
