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
        require_verdict: Keep only rows with a Stage B verdict.
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
    limit: int = _DEFAULT_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        if self.tab not in VALID_TABS:
            raise ValueError(f"unknown jobs view tab: {self.tab!r}")
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
    verdict: str | None
    stage_a_score: int | None
    stage_b_fit_score: int | None
    stage_b_status: str | None


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
        evaluated: Jobs whose Stage A completed that day
            (``evaluations.stage_a_at``).
        applied: Applications recorded that day (``applied.applied_at``).
    """

    day: date
    discovered: int
    evaluated: int
    applied: int


@dataclass(kw_only=True)
class InsightsOverview:
    """Insights aggregate: all-time totals/distributions + a windowed series.

    Attributes:
        window_days: Daily-series window in days ([now - N days, now], UTC).
        total_jobs: All-time job count.
        ml_gate_passed_jobs: All-time gate survivors
            (``ml_gate_result = 'pass'``) — the funnel-stage semantic, not
            gate failures. Jobs never gated count toward neither.
        evaluated_jobs: All-time jobs with a completed Stage A
            (``stage_a_at`` set).
        applied_jobs: All-time recorded applications.
        verdict_distribution: All-time ``stage_b_verdict`` counts plus the
            derived ``below_threshold`` bucket (verdict-less rows with
            ``stage_b_status = 'skipped_below_threshold'`` — the same
            grouping the triage view uses). Only nonzero buckets appear.
        status_distribution: All-time ``job_status.status`` counts. Only
            nonzero buckets appear.
        daily: Ascending per-day funnel counts over the window; only days
            having data appear (consumers zero-fill gaps).
    """

    window_days: int
    total_jobs: int
    ml_gate_passed_jobs: int
    evaluated_jobs: int
    applied_jobs: int
    verdict_distribution: dict[str, int]
    status_distribution: dict[str, int]
    daily: list[InsightsDay]


__all__ = [
    "VALID_TABS",
    "InsightsDay",
    "InsightsOverview",
    "JobsViewPage",
    "JobsViewQuery",
    "JobsViewRow",
    "TwinStatusRow",
]
