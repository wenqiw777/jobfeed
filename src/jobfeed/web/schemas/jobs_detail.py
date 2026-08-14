"""DTOs for ``GET /api/jobs/{id}``: the aggregated detail response."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.user_decisions import UserDecision, decision_for_status
from jobfeed.services.jobs_view import JobDetail
from jobfeed.web.schemas._jobs_detail_stage_b import StageBDetail, stage_b_section


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


class EvaluationDetail(BaseModel):
    """Evaluation section of the detail response (stages optional).

    ``stage_b_status`` is the raw pipeline status (the same store column the
    list rows carry), set even when ``stage_b`` is None — below-threshold
    rows have no Stage B blocks but still need their derived display state.
    """

    stage_a: StageADetail | None
    stage_b: StageBDetail | None
    stage_b_status: str | None


class StatusDetail(BaseModel):
    """Workflow section: current status, notes, follow-up, history."""

    status: str | None
    decision: UserDecision | None
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


def interview_round_response(round_: InterviewRound) -> InterviewRoundDetail:
    """Render a domain interview round as its DTO.

    Shared by the detail response below and the workflow interview routes.

    Args:
        round_: Interview round from the store/workflow service.

    Returns:
        Wire-shape interview round.
    """
    return InterviewRoundDetail(
        round_index=round_.round_index,
        label=round_.label,
        scheduled_at=round_.scheduled_at,
        completed_at=round_.completed_at,
        notes=round_.notes,
    )


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
        interviews=[interview_round_response(round_) for round_ in detail.interviews],
        application=_application_detail(detail),
    )


def _evaluation_detail(detail: JobDetail) -> EvaluationDetail:
    """Map the optional Stage A / Stage B results to the evaluation section."""
    evaluation = detail.evaluation
    stage_a = evaluation.stage_a if evaluation is not None else None
    return EvaluationDetail(
        stage_a=(
            StageADetail(score=stage_a.score, one_line=stage_a.one_line)
            if stage_a is not None
            else None
        ),
        stage_b=stage_b_section(evaluation),
        stage_b_status=evaluation.stage_b_status if evaluation is not None else None,
    )


def _status_detail(detail: JobDetail) -> StatusDetail:
    """Map the status info + history to the workflow section."""
    status = detail.status
    return StatusDetail(
        status=str(status.status) if status is not None else None,
        decision=(
            decision_for_status(str(status.status)) if status is not None else None
        ),
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


__all__ = ["JobDetailResponse", "interview_round_response", "job_detail_response"]
