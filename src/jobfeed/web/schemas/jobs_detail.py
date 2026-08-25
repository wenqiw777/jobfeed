"""DTOs for ``GET /api/jobs/{id}``: the aggregated detail response."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.user_decisions import UserDecision, decision_for_status
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


class EligibilityCheckDetail(BaseModel):
    """One evidence-backed hard eligibility check."""

    kind: str | None = None
    requirement: str | None = None
    status: str | None = None
    candidate_evidence: str | None = None
    reason: str | None = None


class RequirementDetail(BaseModel):
    """One unified requirement-to-resume evidence assessment."""

    requirement: str | None = None
    priority: str | None = None
    category: str | None = None
    match: str | None = None
    resume_evidence: str | None = None
    evidence_type: str | None = None


class EvaluationDetail(BaseModel):
    """Canonical unified evaluation; legacy Stage A/B never participate."""

    summary: str | None
    eligibility_status: str | None
    eligibility_checks: list[EligibilityCheckDetail]
    requirements: list[RequirementDetail]
    match_score: int | None
    match_tier: str | None
    one_line: str | None
    ats_visibility_score: int | None
    evaluator_version: str | None
    model: str | None


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
    """Map only the canonical unified row to the evaluation section."""
    evaluation = detail.evaluation or {}
    result = _mapping(evaluation.get("result_json"))
    return EvaluationDetail(
        summary=_text(result.get("summary")),
        eligibility_status=_text(evaluation.get("eligibility_status")),
        eligibility_checks=[
            EligibilityCheckDetail(
                kind=_text(item.get("kind")),
                requirement=_text(item.get("requirement")),
                status=_text(item.get("status")),
                candidate_evidence=_text(item.get("candidate_evidence")),
                reason=_text(item.get("reason")),
            )
            for item in _mapping_list(result.get("eligibility_checks"))
        ],
        requirements=[
            RequirementDetail(
                requirement=_text(item.get("requirement")),
                priority=_text(item.get("priority")),
                category=_text(item.get("category")),
                match=_text(item.get("match")),
                resume_evidence=_text(item.get("resume_evidence")),
                evidence_type=_text(item.get("evidence_type")),
            )
            for item in _mapping_list(result.get("requirements"))
        ],
        match_score=_integer(evaluation.get("match_score")),
        match_tier=_text(evaluation.get("match_tier")),
        one_line=_text(result.get("one_line")),
        ats_visibility_score=_integer(result.get("ats_visibility_score")),
        evaluator_version=_text(evaluation.get("evaluator_version")),
        model=_text(evaluation.get("model")),
    )


def _mapping(value: object) -> dict[str, object]:
    """Return a string-keyed mapping for decoded JSON objects."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _mapping_list(value: object) -> list[dict[str, object]]:
    """Keep only mapping entries from a decoded JSON array."""
    if not isinstance(value, list):
        return []
    return [_mapping(item) for item in value if isinstance(item, dict)]


def _text(value: object) -> str | None:
    """Return canonical text values without coercing malformed data."""
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    """Return canonical integer values without treating booleans as scores."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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
