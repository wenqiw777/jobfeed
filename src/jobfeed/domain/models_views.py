"""Jobs-view domain models for the Phase 8 web read path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from jobfeed.domain.models import JobPosting

#: The six jobs-view tabs. Triage tabs (queue, pending_jd) exclude closed
#: rows; Library tabs (all, scored, shortlisted, archived) include them.
VALID_TABS: tuple[str, ...] = (
    "queue",
    "pending_jd",
    "all",
    "scored",
    "shortlisted",
    "archived",
)

#: Jobs-view sort names (plan A4). The canonical vocabulary lives here so
#: ``JobsViewQuery`` can validate it; the in-memory sort keys stay in
#: ``services/_jobs_view_sort.py`` and the SQL ORDER BY map in the store.
VALID_SORTS: tuple[str, ...] = (
    "discovered_desc",
    "posted_asc",
    "posted_desc",
    "score_asc",
    "score_desc",
    "company_asc",
)

#: The default sort (newest discovered first).
DEFAULT_SORT = "discovered_desc"

_DEFAULT_LIMIT = 50


@dataclass(frozen=True, kw_only=True)
class JobsViewQuery:
    """Filter and pagination parameters for the jobs view.

    The explicit ``statuses`` filter AND-narrows whichever tab is selected;
    ``search``/``posted_within_days``/``require_verdict`` apply to the rows,
    the total, and every ``tab_counts`` bucket alike.

    Attributes:
        tab: One of ``VALID_TABS``.
        statuses: Optional explicit status filter (AND-composed with the tab).
            An empty tuple means no status narrowing (same as ``None``).
        search: Case-insensitive substring matched against company OR title.
        posted_within_days: Freshness window in days. Cuts on
            ``discovered_at`` (not ``posted_at``) per rewrite-spec §15 — a job
            posted long ago but newly scraped is fresh to the user's workflow.
        require_verdict: Keep only rows with a completed unified match tier.
        sort: One of ``VALID_SORTS``. Orders the SQL rows so plain Library
            requests paginate correctly in SQL beyond any corpus cap.
        limit: Maximum rows returned. With ``offset``, windows the SQL
            prefilter — BEFORE any in-memory hard filter or display fold.
            Callers that fold or hard-filter post-query (triage tabs, plan
            D10) must request the full corpus (large limit) and paginate
            post-fold, or the fold corpus is silently truncated.
        offset: Rows to skip before the returned window.
    """

    tab: str
    statuses: tuple[str, ...] | None = None
    search: str | None = None
    posted_within_days: int | None = None
    require_verdict: bool = False
    sort: str = DEFAULT_SORT
    limit: int = _DEFAULT_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        if self.tab not in VALID_TABS:
            raise ValueError(f"unknown jobs view tab: {self.tab!r}")
        if self.sort not in VALID_SORTS:
            raise ValueError(f"unknown jobs view sort: {self.sort!r}")
        if self.limit < 0:
            raise ValueError(f"negative jobs view limit: {self.limit}")
        if self.offset < 0:
            raise ValueError(f"negative jobs view offset: {self.offset}")


@dataclass(kw_only=True)
class JobsViewRow:
    """One jobs-view row: a job joined with its status and evaluation summary.

    ``jd_quality``/``posted_at``/``discovered_at``/``closed_at`` ride on
    ``job``; the persisted soft key (``company_norm``/``title_norm``) is not a
    ``JobPosting`` field, so it is carried here.
    """

    job: JobPosting
    company_norm: str | None
    title_norm: str | None
    status: str
    evaluation_score: int | None
    evaluation_verdict: str | None
    evaluation_status: str | None
    evaluator_version: str | None


@dataclass(frozen=True, kw_only=True)
class TwinStatusRow:
    """One twin posting of a detail job: identity, platform, link, status.

    Twins share the persisted non-blank ``(company_norm, title_norm)`` soft
    key with the detail job; the job itself is never one of its own twins.
    """

    job_id: str
    platform: str
    url: str
    status: str


@dataclass(kw_only=True)
class JobsViewPage:
    """A bounded jobs-view result page.

    Attributes:
        rows: The active tab's rows within the requested window.
        total: Full match count for the active tab (ignores limit/offset).
        tab_counts: Per-tab totals (one entry per ``VALID_TABS`` value) under
            the same request filters.
    """

    rows: list[JobsViewRow]
    total: int
    tab_counts: dict[str, int]


@dataclass(frozen=True, kw_only=True)
class InsightsDay:
    """One UTC day of the insights series: per-day funnel counts.

    Attributes:
        day: UTC calendar day of the bucket.
        discovered: Jobs first discovered that day (``jobs.discovered_at``).
        evaluated: Jobs whose unified evaluation completed that day
            (``evaluation_results.evaluated_at``).
        applied: Cohort jobs currently in the Applied decision, bucketed by
            their latest transition to applied.
    """

    day: date
    discovered: int
    evaluated: int
    applied: int


@dataclass(kw_only=True)
class InsightsOverview:
    """Insights aggregate for one discovery-date cohort and daily event series.

    Attributes:
        window_days: Daily-series window in days ([now - N days, now], UTC),
            or None for all time through now.
        total_jobs: Jobs discovered within the requested window.
        ml_gate_passed_jobs: Cohort gate survivors
            (``ml_gate_result = 'pass'``) — the funnel-stage semantic, not
            gate failures. Jobs never gated count toward neither.
        evaluated_jobs: Cohort jobs with a completed unified evaluation.
        detailed_reviewed_jobs: Compatibility alias for ``evaluated_jobs``;
            unified evaluation has no separate detailed-review stage.
        applied_jobs: Cohort jobs whose current workflow state belongs to the
            user-facing Applied decision.
        verdict_distribution: Cohort unified ``match_tier`` counts for
            completed evaluations. Only nonzero buckets appear.
        decision_distribution: Cohort current user-decision counts. Historical
            ``archived`` rows are included in the single ``ignored`` bucket.
        daily: Ascending per-day event counts for cohort jobs; only days having
            data appear (consumers zero-fill gaps).
    """

    window_days: int | None
    total_jobs: int
    ml_gate_passed_jobs: int
    evaluated_jobs: int
    detailed_reviewed_jobs: int
    applied_jobs: int
    verdict_distribution: dict[str, int]
    decision_distribution: dict[str, int]
    daily: list[InsightsDay]


__all__ = [
    "DEFAULT_SORT",
    "VALID_SORTS",
    "VALID_TABS",
    "InsightsDay",
    "InsightsOverview",
    "JobsViewPage",
    "JobsViewQuery",
    "JobsViewRow",
    "TwinStatusRow",
]
