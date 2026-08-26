"""DTOs for ``GET /api/jobs``: query params and the list response."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from jobfeed.domain.models_views import JobsViewPage, JobsViewQuery, JobsViewRow
from jobfeed.domain.user_decisions import (
    UserDecision,
    decision_for_status,
    statuses_for_decision,
)
from jobfeed.services.jobs_view import JOBS_VIEW_CORPUS_LIMIT

_DEFAULT_PAGE_LIMIT = 50

TabName = Literal["queue", "pending_jd", "all", "scored", "shortlisted", "archived"]
SortName = Literal[
    "discovered_desc",
    "posted_asc",
    "posted_desc",
    "score_asc",
    "score_desc",
    "company_asc",
]


class JobsListParams(BaseModel):
    """Query parameters of ``GET /api/jobs`` (plan A4).

    ``apply_hard_filters`` and ``dedupe`` default to false — the raw endpoint
    is neutral; the frontend's typed param builders set them per zone.
    ``sort`` is honored by both Library and Triage. Triage uses the fixed
    verdict-group order only when the default discovered sort is requested.
    """

    tab: TabName = "all"
    statuses: list[str] | None = None
    decision: UserDecision | None = None
    search: str | None = None
    posted_within_days: int | None = Field(default=None, ge=0)
    require_verdict: bool = False
    apply_hard_filters: bool = False
    dedupe: bool = False
    fast: bool = False
    sort: SortName = "discovered_desc"
    limit: int = Field(default=_DEFAULT_PAGE_LIMIT, ge=0, le=JOBS_VIEW_CORPUS_LIMIT)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_status_filter(self) -> JobsListParams:
        if self.statuses and self.decision is not None:
            raise ValueError("statuses and decision cannot be combined")
        return self

    def to_query(self) -> JobsViewQuery:
        """Build the domain query from the request parameters.

        Returns:
            Domain query carrying the tab, filters, and pagination window.
        """
        return JobsViewQuery(
            tab=self.tab,
            statuses=(
                statuses_for_decision(self.decision)
                if self.decision is not None
                else tuple(self.statuses)
                if self.statuses
                else None
            ),
            search=self.search,
            posted_within_days=self.posted_within_days,
            require_verdict=self.require_verdict,
            limit=self.limit,
            offset=self.offset,
        )


class JobSummary(BaseModel):
    """One jobs-list row (the DTO form of ``JobsViewRow``)."""

    id: str
    company: str
    title: str
    platform: str
    url: str
    status: str
    decision: UserDecision | None
    verdict: str | None
    stage_a_score: int | None
    stage_b_fit_score: int | None
    stage_b_status: str | None
    posted_at: datetime | None
    discovered_at: datetime
    closed_at: datetime | None
    jd_quality: str | None
    company_norm: str | None
    title_norm: str | None


class JobsListResponse(BaseModel):
    """``GET /api/jobs`` response: rows, true total, SQL tab counts."""

    jobs: list[JobSummary]
    total: int
    tab_counts: dict[str, int]
    total_is_exact: bool = True


def jobs_list_response(page: JobsViewPage) -> JobsListResponse:
    """Render a domain page as the list response (``rows`` -> ``jobs``).

    Args:
        page: Composed jobs view page from the service.

    Returns:
        Wire-shape list response.
    """
    return JobsListResponse(
        jobs=[_job_summary(row) for row in page.rows],
        total=page.total,
        tab_counts=page.tab_counts,
        total_is_exact=page.total_is_exact,
    )


def _job_summary(row: JobsViewRow) -> JobSummary:
    """Map one view row to its summary DTO."""
    job = row.job
    return JobSummary(
        id=job.id or "",
        company=job.company,
        title=job.title,
        platform=job.platform,
        url=job.url,
        status=row.status,
        decision=decision_for_status(row.status),
        verdict=row.verdict,
        stage_a_score=row.stage_a_score,
        stage_b_fit_score=row.stage_b_fit_score,
        stage_b_status=row.stage_b_status,
        posted_at=job.posted_at,
        discovered_at=job.discovered_at,
        closed_at=job.closed_at,
        jd_quality=job.jd_quality.value if job.jd_quality else None,
        company_norm=row.company_norm,
        title_norm=row.title_norm,
    )


__all__ = [
    "JobSummary",
    "JobsListParams",
    "JobsListResponse",
    "SortName",
    "TabName",
    "jobs_list_response",
]
