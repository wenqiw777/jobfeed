"""DTOs for ``GET /api/jobs/{id}``: the aggregated detail response."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jobfeed.domain.models import StageBResult
from jobfeed.services.jobs_view import JobDetail


class JobDetailJob(BaseModel):
    """The detail job record (full posting incl. JD text)."""

    id: str
    platform: str
    canonical_id: str
    url: str
    title: str
    company: str
    location: str
    discovered_at: datetime
    posted_at: datetime | None
    closed_at: datetime | None
    jd_quality: str | None
    jd_text: str | None


class StageADetail(BaseModel):
    """Stage A summary: fast score plus its one-line rationale."""

    score: int
    one_line: str


class StrengthDetail(BaseModel):
    """One matched requirement with its resume evidence."""

    requirement: str
    evidence: str


class GapDetail(BaseModel):
    """One missing/weak requirement with severity and mitigation."""

    requirement: str
    severity: str
    mitigation: str


class ResumeHooksDetail(BaseModel):
    """The three resume-hook blocks of the Stage B output."""

    lead_with: str
    supporting: list[str]
    avoid_mentioning: list[str]


class StageBDetail(BaseModel):
    """Stage B blocks: verdict, JD summary, fit analysis, resume hooks."""

    verdict: str
    jd_summary: str
    fit_score: int
    strengths: list[StrengthDetail]
    gaps: list[GapDetail]
    hooks: ResumeHooksDetail


class EvaluationDetail(BaseModel):
    """Evaluation section of the detail response (stages optional)."""

    stage_a: StageADetail | None
    stage_b: StageBDetail | None


class StatusDetail(BaseModel):
    """Workflow section: current status, notes, follow-up, history."""

    status: str | None
    notes: str | None
    next_followup_at: datetime | None
    resume_variant: str | None
    history: list[str]


class TwinDetail(BaseModel):
    """One twin posting (same company_norm + title_norm) of the detail job."""

    job_id: str
    platform: str
    url: str
    status: str


class InterviewRoundDetail(BaseModel):
    """One interview round of the detail job."""

    round_index: int
    label: str
    scheduled_at: datetime | None
    completed_at: datetime | None
    notes: str | None


class ApplicationDetail(BaseModel):
    """Application audit refs: snapshot hashes only, never contents."""

    applied_at: datetime
    application_method: str | None
    master_resume_hash: str | None
    tailored_resume_hash: str | None


class JobDetailResponse(BaseModel):
    """``GET /api/jobs/{id}`` response."""

    job: JobDetailJob
    evaluation: EvaluationDetail
    status: StatusDetail
    twins: list[TwinDetail]
    interviews: list[InterviewRoundDetail]
    application: ApplicationDetail | None


def job_detail_response(detail: JobDetail) -> JobDetailResponse:
    """Render the aggregated domain detail as the detail response.

    Args:
        detail: Aggregated detail from the jobs view service.

    Returns:
        Wire-shape detail response.
    """
    job = detail.job
    return JobDetailResponse(
        job=JobDetailJob(
            id=job.id or "",
            platform=job.platform,
            canonical_id=job.canonical_id,
            url=job.url,
            title=job.title,
            company=job.company,
            location=job.location,
            discovered_at=job.discovered_at,
            posted_at=job.posted_at,
            closed_at=job.closed_at,
            jd_quality=job.jd_quality.value if job.jd_quality else None,
            jd_text=job.jd_text,
        ),
        evaluation=_evaluation_detail(detail),
        status=_status_detail(detail),
        twins=[
            TwinDetail(
                job_id=twin.job_id,
                platform=twin.platform,
                url=twin.url,
                status=twin.status,
            )
            for twin in detail.twins
        ],
        interviews=[
            InterviewRoundDetail(
                round_index=round_.round_index,
                label=round_.label,
                scheduled_at=round_.scheduled_at,
                completed_at=round_.completed_at,
                notes=round_.notes,
            )
            for round_ in detail.interviews
        ],
        application=_application_detail(detail),
    )


def _evaluation_detail(detail: JobDetail) -> EvaluationDetail:
    """Map the optional Stage A / Stage B results to the evaluation section."""
    evaluation = detail.evaluation
    stage_a = evaluation.stage_a if evaluation is not None else None
    stage_b = evaluation.stage_b if evaluation is not None else None
    return EvaluationDetail(
        stage_a=(
            StageADetail(score=stage_a.score, one_line=stage_a.one_line)
            if stage_a is not None
            else None
        ),
        stage_b=_stage_b_detail(stage_b) if stage_b is not None else None,
    )


def _status_detail(detail: JobDetail) -> StatusDetail:
    """Map the status info + history to the workflow section."""
    status = detail.status
    return StatusDetail(
        status=str(status.status) if status is not None else None,
        notes=status.notes if status is not None else None,
        next_followup_at=status.next_followup_at if status is not None else None,
        resume_variant=status.resume_variant if status is not None else None,
        history=detail.history,
    )


def _application_detail(detail: JobDetail) -> ApplicationDetail | None:
    """Map the application record to its snapshot-refs DTO (hashes only)."""
    record = detail.application
    if record is None:
        return None
    return ApplicationDetail(
        applied_at=record.applied_at,
        application_method=record.application_method,
        master_resume_hash=record.master_resume_hash,
        tailored_resume_hash=record.tailored_resume_hash,
    )


def _stage_b_detail(stage_b: StageBResult) -> StageBDetail:
    """Map a Stage B result to its DTO, lifting the three hook blocks."""
    fit = stage_b.fit_analysis
    return StageBDetail(
        verdict=stage_b.verdict.value,
        jd_summary=stage_b.jd_summary,
        fit_score=fit.score,
        strengths=[
            StrengthDetail(requirement=item.requirement, evidence=item.evidence)
            for item in fit.strengths
        ],
        gaps=[
            GapDetail(
                requirement=item.requirement,
                severity=item.severity,
                mitigation=item.mitigation,
            )
            for item in fit.gaps
        ],
        hooks=_resume_hooks_detail(stage_b),
    )


def _resume_hooks_detail(stage_b: StageBResult) -> ResumeHooksDetail:
    """Read the lead_with/supporting/avoid_mentioning hook blocks.

    Prefers the persisted ``raw_blocks['resume_hooks']`` object; when it is
    absent or malformed, falls back to the normalized ``resume_hooks`` list
    (first item leads, the rest support — mirroring the store's derived
    block) with no avoid-mentioning entries.
    """
    blocks = stage_b.raw_blocks or {}
    hooks_block = blocks.get("resume_hooks")
    if isinstance(hooks_block, dict) and "lead_with" in hooks_block:
        return ResumeHooksDetail(
            lead_with=str(hooks_block.get("lead_with") or ""),
            supporting=_string_list(hooks_block.get("supporting")),
            avoid_mentioning=_string_list(hooks_block.get("avoid_mentioning")),
        )
    flat = stage_b.resume_hooks
    return ResumeHooksDetail(
        lead_with=flat[0] if flat else "",
        supporting=list(flat[1:]),
        avoid_mentioning=[],
    )


def _string_list(value: object) -> list[str]:
    """Coerce a raw JSON value to a list of strings (non-lists -> empty)."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


__all__ = ["JobDetailResponse", "job_detail_response"]
