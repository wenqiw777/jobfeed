"""DTOs for ``GET /api/insights/overview`` and ``GET /api/attention``."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from jobfeed.domain.models import ApplicationStats, ResumeVariantStats
from jobfeed.domain.models_ops import AttentionItem, AttentionReport
from jobfeed.domain.models_status import WorkflowAttention, WorkflowAttentionItem
from jobfeed.domain.models_views import InsightsDay
from jobfeed.services.insights import InsightsBundle


class InsightsTotals(BaseModel):
    """All-time funnel totals: discovered, gate-passed, evaluated, applied.

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


class ResumeVariantStatsRow(BaseModel):
    """Per-resume-variant application outcomes."""

    sent: int
    responses: int
    interviews: int
    offers: int
    rejections: int


class ApplicationStatsBlock(BaseModel):
    """Windowed application stats including the by-resume table.

    ``by_resume`` is empty (never null) when no application falls inside
    the window.
    """

    applied_count: int
    response_count: int
    interview_count: int
    offer_count: int
    rejection_count: int
    median_days_to_response: float | None
    by_resume: dict[str, ResumeVariantStatsRow]


class InsightsOverviewResponse(BaseModel):
    """``GET /api/insights/overview`` response.

    Totals and distributions are all-time; ``daily`` and ``applications``
    cover the requested window. ``verdict_distribution`` includes the derived
    ``below_threshold`` bucket (triage grouping); both distributions carry
    only nonzero buckets.
    """

    window_days: int
    totals: InsightsTotals
    verdict_distribution: dict[str, int]
    status_distribution: dict[str, int]
    daily: list[InsightsDayEntry]
    applications: ApplicationStatsBlock


def insights_overview_response(bundle: InsightsBundle) -> InsightsOverviewResponse:
    """Render the composed insights bundle as the overview response.

    Args:
        bundle: Store aggregate plus windowed application stats.

    Returns:
        Wire-shape overview response.
    """
    overview = bundle.overview
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
        applications=_application_stats_block(bundle.applications),
    )


def _day_entry(day: InsightsDay) -> InsightsDayEntry:
    """Map one domain day bucket to its DTO."""
    return InsightsDayEntry(
        day=day.day,
        discovered=day.discovered,
        evaluated=day.evaluated,
        applied=day.applied,
    )


def _application_stats_block(stats: ApplicationStats) -> ApplicationStatsBlock:
    """Map domain application stats to the response block (None -> {})."""
    by_resume = stats.by_resume or {}
    return ApplicationStatsBlock(
        applied_count=stats.applied_count,
        response_count=stats.response_count,
        interview_count=stats.interview_count,
        offer_count=stats.offer_count,
        rejection_count=stats.rejection_count,
        median_days_to_response=stats.median_days_to_response,
        by_resume={name: _variant_row(variant) for name, variant in by_resume.items()},
    )


def _variant_row(variant: ResumeVariantStats) -> ResumeVariantStatsRow:
    """Map one per-variant stats record to its DTO."""
    return ResumeVariantStatsRow(
        sent=variant.sent,
        responses=variant.responses,
        interviews=variant.interviews,
        offers=variant.offers,
        rejections=variant.rejections,
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
    "ApplicationStatsBlock",
    "AttentionResponse",
    "InsightsDayEntry",
    "InsightsOverviewResponse",
    "InsightsTotals",
    "PipelineAttentionEntry",
    "ResumeVariantStatsRow",
    "WorkflowAttentionEntry",
    "attention_response",
    "insights_overview_response",
]
