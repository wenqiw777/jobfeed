"""Canonical Triage DTOs with explicit parent and source identifiers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from jobfeed.domain.user_decisions import UserDecision, decision_for_status
from jobfeed.web.schemas._jobs_detail_stage_b import ResumeHooksDetail, StageBDetail
from jobfeed.web.schemas.jobs_detail import (
    EvaluationDetail,
    InterviewRoundDetail,
    JobDetailJob,
    StageADetail,
    StatusDetail,
)
from jobfeed.web.schemas.jobs_list import JobsListResponse, JobSummary


class RealJobsSelectionParams(BaseModel):
    """Filters shared by the canonical list and one-shot selection routes."""

    decision: UserDecision = "results"
    sort: Literal[
        "triage_posted_asc",
        "triage_posted_desc",
        "triage_score_asc",
        "triage_score_desc",
        "discovered_desc",
    ] = "triage_posted_desc"
    search: str | None = None
    require_verdict: bool = False


class RealJobsListParams(RealJobsSelectionParams):
    """Canonical list filters plus a bounded page window."""

    limit: int = Field(default=25, ge=0, le=10_000)
    offset: int = Field(default=0, ge=0)


class RealJobsSelectionResponse(BaseModel):
    """Canonical IDs and exact total captured in one selection snapshot."""

    real_job_ids: list[str]
    total: int


class RealJobSummary(JobSummary):
    """One canonical Triage card with explicit representative source ID."""

    real_job_id: str
    source_job_id: str
    identity_review_state: str
    priority_score: float | None = None
    queue_tier: int | None = None


class RealJobsListResponse(BaseModel):
    """Canonical page, exact total, and decision-tab counts."""

    jobs: list[RealJobSummary]
    total: int
    tab_counts: dict[str, int]
    total_is_exact: bool = True


class RealJobSource(BaseModel):
    """One source posting attached to a canonical parent."""

    job_id: str
    platform: str
    url: str
    apply_url: str | None
    title: str
    discovered_at: datetime
    posted_at: datetime | None
    closed_at: datetime | None
    is_repost: bool | None


class IdentityEvidence(BaseModel):
    """Observed external identifier linking a source to its parent."""

    provider: str
    scope: str
    native_id: str
    evidence_job_id: str
    observed_url: str | None


class RealJobDetailResponse(BaseModel):
    """Canonical detail with current decision and all source evidence."""

    real_job_id: str
    identity_review_state: str
    evaluation_stale_reason: str | None = None
    stale_stage_a_score: int | None = None
    job: JobDetailJob
    evaluation: EvaluationDetail
    status: StatusDetail
    twins: list[dict[str, str]]
    interviews: list[InterviewRoundDetail]
    application: None = None
    sources: list[RealJobSource]
    identity_evidence: list[IdentityEvidence]


def real_jobs_list_response(payload: dict[str, object]) -> RealJobsListResponse:
    """Convert canonical SQL rows into typed Triage cards.

    Args:
        payload: SQL-backed canonical or source view payload.

    Returns:
        Validated canonical Triage response.
    """
    rows = payload["jobs"]
    assert isinstance(rows, list)
    jobs = []
    for row in rows:
        assert isinstance(row, dict)
        status = str(row["status"])
        jobs.append(
            RealJobSummary(
                id=str(row["real_job_id"]),
                real_job_id=str(row["real_job_id"]),
                source_job_id=str(row["source_job_id"]),
                company=str(row["company"]),
                title=str(row["title"]),
                location=str(row["location"] or ""),
                platform=str(row["platform"]),
                url=str(row["url"]),
                status=status,
                decision=decision_for_status(status),
                verdict=row["stage_b_verdict"],
                stage_a_score=row["stage_a_score"],
                stage_b_fit_score=row["fit_score"],
                stage_b_status=row["stage_b_status"],
                posted_at=row["posted_at"],
                discovered_at=row["discovered_at"],
                closed_at=row["closed_at"],
                jd_quality=row["jd_quality"],
                company_norm=row["company_norm"],
                title_norm=row["title_norm"],
                is_repost=bool(row["is_repost"])
                if row["is_repost"] is not None
                else None,
                repost_evidence=row["repost_evidence"],
                repost_observed_at=row["repost_observed_at"],
                identity_review_state=str(row["identity_review_state"]),
                evaluation_stale_reason=row.get("evaluation_stale_reason"),
                priority_score=row.get("priority_score"),
                queue_tier=row.get("queue_tier"),
            )
        )
    return RealJobsListResponse.model_validate(
        {"jobs": jobs, "total": payload["total"], "tab_counts": payload["tab_counts"]}
    )


def source_library_response(payload: dict[str, object]) -> JobsListResponse:
    """Convert source SQL rows into typed Library cards.

    Args:
        payload: SQL-backed canonical or source view payload.

    Returns:
        Validated source Library response.
    """
    rows = payload["jobs"]
    assert isinstance(rows, list)
    jobs = []
    for row in rows:
        assert isinstance(row, dict)
        status = str(row["status"])
        jobs.append(
            JobSummary(
                id=str(row["source_job_id"]),
                real_job_id=(
                    str(row["real_job_id"]) if row["real_job_id"] is not None else None
                ),
                evaluation_stale_reason=row.get("evaluation_stale_reason"),
                company=row["company"],
                title=row["title"],
                location=row["location"] or "",
                platform=row["platform"],
                url=row["url"],
                status=status,
                decision=decision_for_status(status),
                verdict=row["stage_b_verdict"],
                stage_a_score=row["stage_a_score"],
                stage_b_fit_score=row["fit_score"],
                stage_b_status=row["stage_b_status"],
                posted_at=row["posted_at"],
                discovered_at=row["discovered_at"],
                closed_at=row["closed_at"],
                jd_quality=row["jd_quality"],
                company_norm=row["company_norm"],
                title_norm=row["title_norm"],
                is_repost=bool(row["is_repost"])
                if row["is_repost"] is not None
                else None,
                repost_evidence=row["repost_evidence"],
                repost_observed_at=row["repost_observed_at"],
            )
        )
    return JobsListResponse.model_validate(
        {"jobs": jobs, "total": payload["total"], "tab_counts": payload["tab_counts"]}
    )


def real_job_detail_response(
    payload: dict[str, object], *, history: list[str], interviews: list[object]
) -> RealJobDetailResponse:
    """Convert representative, status, and source evidence into detail.

    Args:
        payload: SQL-backed canonical or source view payload.
        history: Canonical status history for the detail response.
        interviews: Canonical interview rounds for the detail response.

    Returns:
        Validated canonical detail response.
    """
    row = payload["row"]
    sources = payload["sources"]
    evidence = payload["identity_evidence"]
    assert isinstance(row, dict)
    assert isinstance(sources, list)
    assert isinstance(evidence, list)
    raw_stage_b = json.loads(row["stage_b_json"]) if row["stage_b_json"] else None
    fit = raw_stage_b.get("fit_analysis", {}) if raw_stage_b else {}
    blocks = raw_stage_b.get("raw_blocks") if raw_stage_b else None
    hooks = blocks.get("resume_hooks") if isinstance(blocks, dict) else None
    stage_b = (
        StageBDetail(
            verdict=row["stage_b_verdict"] or "",
            jd_summary=raw_stage_b.get("jd_summary"),
            fit_score=fit.get("score"),
            strengths=fit.get("strengths") or [],
            gaps=fit.get("gaps") or [],
            hooks=ResumeHooksDetail.model_validate(
                hooks or {"lead_with": "", "supporting": [], "avoid_mentioning": []}
            ),
        )
        if raw_stage_b
        else None
    )
    return RealJobDetailResponse(
        real_job_id=str(row["real_job_id"]),
        identity_review_state=str(row["identity_review_state"]),
        evaluation_stale_reason=row.get("evaluation_stale_reason"),
        stale_stage_a_score=row.get("stale_stage_a_score"),
        job=JobDetailJob(
            id=str(row["id"]),
            platform=row["platform"],
            canonical_id=row["canonical_id"],
            url=row["url"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            discovered_at=row["discovered_at"],
            posted_at=row["posted_at"],
            closed_at=row["closed_at"],
            jd_quality=row["jd_quality"],
            jd_text=row["jd_text"],
        ),
        evaluation=EvaluationDetail(
            stage_a=(
                StageADetail(
                    score=row["stage_a_score"], one_line=row["stage_a_one_line"] or ""
                )
                if row["stage_a_score"] is not None
                else None
            ),
            stage_b=stage_b,
            stage_b_status=row["stage_b_status"],
        ),
        status=StatusDetail(
            status=row["status"],
            decision=decision_for_status(row["status"]),
            notes=row["notes"],
            next_followup_at=row["next_followup_at"],
            resume_variant=row["resume_variant"],
            history=history,
        ),
        twins=[],
        interviews=[InterviewRoundDetail.model_validate(item) for item in interviews],
        sources=[
            RealJobSource.model_validate({**source, "job_id": str(source["job_id"])})
            for source in sources
        ],
        identity_evidence=[
            IdentityEvidence.model_validate(
                {**item, "evidence_job_id": str(item["evidence_job_id"])}
            )
            for item in evidence
        ],
    )
