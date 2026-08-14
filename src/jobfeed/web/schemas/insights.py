"""DTOs for ``GET /api/insights/overview`` and ``GET /api/attention``."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from jobfeed.domain.models_ops import AttentionItem, AttentionReport
from jobfeed.domain.models_status import WorkflowAttention, WorkflowAttentionItem
from jobfeed.domain.models_views import InsightsDay, InsightsOverview


class InsightsTotals(BaseModel):
    """Selected-cohort totals: discovered, gate-passed, evaluated, applied.

    ``ml_gate_passed`` counts gate survivors (``ml_gate_result = 'pass'``) —
    the funnel-stage semantic, not gate failures; jobs never gated count
    toward neither.
    """

    jobs: int
    ml_gate_passed: int
    evaluated: int
    applied: int


class InsightsDayEntry(BaseModel):
    """One UTC day of the windowed series (only days having data appear)."""

    day: date
    discovered: int
    evaluated: int
    applied: int


class InsightsOverviewResponse(BaseModel):
    """``GET /api/insights/overview`` response.

    The requested window selects the discovery-date cohort for totals,
    distributions, and ``daily``.
    ``verdict_distribution`` includes the derived
    ``below_threshold`` bucket (triage grouping); both distributions carry
    only nonzero buckets.
    """

    window_days: int | None
    totals: InsightsTotals
    verdict_distribution: dict[str, int]
    status_distribution: dict[str, int]
    daily: list[InsightsDayEntry]


def insights_overview_response(
    overview: InsightsOverview,
) -> InsightsOverviewResponse:
    """Render the insights aggregate as the overview response.

    Args:
        overview: Selected-period insights aggregate.

    Returns:
        Wire-shape overview response.
    """
    return InsightsOverviewResponse(
        window_days=overview.window_days,
        totals=InsightsTotals(
            jobs=overview.total_jobs,
            ml_gate_passed=overview.ml_gate_passed_jobs,
            evaluated=overview.evaluated_jobs,
            applied=overview.applied_jobs,
        ),
        verdict_distribution=overview.verdict_distribution,
        status_distribution=overview.status_distribution,
        daily=[_day_entry(day) for day in overview.daily],
    )


def _day_entry(day: InsightsDay) -> InsightsDayEntry:
    """Map one domain day bucket to its DTO."""
    return InsightsDayEntry(
        day=day.day,
        discovered=day.discovered,
        evaluated=day.evaluated,
        applied=day.applied,
    )


class WorkflowAttentionEntry(BaseModel):
    """One workflow attention row (follow-up, interview prep, ghosting)."""

    job_id: str
    title: str
    company: str
    url: str
    status: str
    last_status_change_at: datetime
    next_followup_at: datetime | None
    notes: str | None
    reason: str
    days_since: int


class PipelineAttentionEntry(BaseModel):
    """One pipeline-health row (enrich error, low quality, stuck scoring)."""

    job_id: str
    title: str
    company: str
    category: str
    detail: str


class AttentionResponse(BaseModel):
    """``GET /api/attention``: the digest footer's six buckets.

    Three workflow buckets from ``workflow_attention()`` plus three
    pipeline-health buckets from ``needs_attention()``, both with
    store-default thresholds — the same two calls the digest footer makes.
    """

    follow_up_today: list[WorkflowAttentionEntry]
    interview_prep: list[WorkflowAttentionEntry]
    going_ghosted: list[WorkflowAttentionEntry]
    enrich_errors: list[PipelineAttentionEntry]
    low_quality_scored: list[PipelineAttentionEntry]
    stuck_scoring: list[PipelineAttentionEntry]


def attention_response(
    attention: WorkflowAttention, report: AttentionReport
) -> AttentionResponse:
    """Render the two domain attention reports as one six-bucket response.

    Args:
        attention: Three-bucket workflow attention report.
        report: Three-category pipeline health report.

    Returns:
        Wire-shape attention response.
    """
    return AttentionResponse(
        follow_up_today=[_workflow_entry(i) for i in attention.follow_up_today],
        interview_prep=[_workflow_entry(i) for i in attention.interview_prep],
        going_ghosted=[_workflow_entry(i) for i in attention.going_ghosted],
        enrich_errors=[_pipeline_entry(i) for i in report.enrich_errors],
        low_quality_scored=[_pipeline_entry(i) for i in report.low_quality_scored],
        stuck_scoring=[_pipeline_entry(i) for i in report.stuck_scoring],
    )


def _workflow_entry(item: WorkflowAttentionItem) -> WorkflowAttentionEntry:
    """Map one workflow attention item to its DTO."""
    return WorkflowAttentionEntry(
        job_id=item.job_id,
        title=item.title,
        company=item.company,
        url=item.url,
        status=item.status,
        last_status_change_at=item.last_status_change_at,
        next_followup_at=item.next_followup_at,
        notes=item.notes,
        reason=item.reason,
        days_since=item.days_since,
    )


def _pipeline_entry(item: AttentionItem) -> PipelineAttentionEntry:
    """Map one pipeline health item to its DTO."""
    return PipelineAttentionEntry(
        job_id=item.job_id,
        title=item.title,
        company=item.company,
        category=item.category,
        detail=item.detail,
    )


__all__ = [
    "AttentionResponse",
    "InsightsDayEntry",
    "InsightsOverviewResponse",
    "InsightsTotals",
    "PipelineAttentionEntry",
    "WorkflowAttentionEntry",
    "attention_response",
    "insights_overview_response",
]
