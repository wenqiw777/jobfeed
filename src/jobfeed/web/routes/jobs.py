"""Jobs routes: list view and detail aggregation (thin parse/format shell).

All composition (hard filters, fold, requested sort, pagination) lives in
``services/jobs_view.py``; these handlers only parse parameters, call the
service, and render DTOs.
"""

from __future__ import annotations

from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from jobfeed.domain.models_status import StatusInfo
from jobfeed.domain.user_decisions import decision_for_status
from jobfeed.services._evaluate_canonical import _PolicySnapshot
from jobfeed.services.jobs_view import JobsViewService
from jobfeed.web.deps import (
    get_current_scoring_policy,
    get_jobs_view_service,
    get_store,
)
from jobfeed.web.schemas import (
    JobDetailResponse,
    JobsListParams,
    JobsListResponse,
    job_detail_response,
    jobs_list_response,
)
from jobfeed.web.schemas.real_jobs import (
    real_job_detail_response,
    source_library_response,
)

_HTTP_NOT_FOUND = 404

router = APIRouter()


class _CanonicalStatusPort(Protocol):
    async def resolve_real_job_id(self, source_job_id: str) -> str | None: ...
    async def get_real_job_status(self, real_job_id: str) -> StatusInfo | None: ...
    async def get_real_job_status_history(self, real_job_id: str) -> list[str]: ...
    async def query_source_library(self, **kwargs: object) -> dict[str, object]: ...
    async def get_real_job_view(
        self, real_job_id: str, **kwargs: object
    ) -> dict[str, object] | None: ...


@router.get("/jobs/{job_id}/audit")
async def get_source_job_audit(
    job_id: int,
    service: Annotated[JobsViewService, Depends(get_jobs_view_service)],
) -> JobDetailResponse:
    """Read the retained source evaluation and workflow audit by source ID.

    Args:
        job_id: Source posting identifier.
        service: Injected application or job-view service.

    Returns:
        Retained source evaluation and workflow audit response.

    Raises:
        HTTPException: If the source posting does not exist.
    """
    detail = await service.get_job_detail(str(job_id))
    if detail is None:
        raise HTTPException(
            status_code=_HTTP_NOT_FOUND, detail=f"job {job_id} not found"
        )
    return job_detail_response(detail)


@router.get("/jobs")
async def list_jobs(
    params: Annotated[JobsListParams, Query()],
    service: Annotated[JobsViewService, Depends(get_jobs_view_service)],
    store: Annotated[object, Depends(get_store)],
    policy: Annotated[_PolicySnapshot, Depends(get_current_scoring_policy)],
) -> JobsListResponse:
    """List jobs for one tab with optional filters, fold, sort, pagination.

    ``tab_counts`` apply ALL request filters (statuses, search, freshness,
    require_verdict), so sidebar/global counts should come from requests
    WITHOUT ``statuses``/``require_verdict`` narrowing.

    Args:
        params: Validated query parameters (plan A4 contract).
        service: Shared jobs view service from the app state.

    Returns:
        Jobs page: ``jobs`` (the view rows), true ``total``, ``tab_counts``.
    """
    if params.canonical:
        canonical = cast(_CanonicalStatusPort, store)
        payload = await canonical.query_source_library(
            decision=params.decision,
            sort=params.sort,
            search=params.search,
            limit=params.limit,
            offset=params.offset,
            stage_a_policy=policy.stage_a(),
            stage_b_policy=policy.stage_b(),
        )
        return source_library_response(payload)
    page = await service.list_jobs(
        params.to_query(),
        apply_hard_filters=params.apply_hard_filters,
        dedupe=params.dedupe,
        sort=params.sort,
        fast=params.fast,
    )
    return jobs_list_response(page)


@router.get("/jobs/{job_id}")
async def get_job_detail(
    job_id: int,
    service: Annotated[JobsViewService, Depends(get_jobs_view_service)],
    store: Annotated[object, Depends(get_store)],
    policy: Annotated[_PolicySnapshot, Depends(get_current_scoring_policy)],
) -> JobDetailResponse:
    """Aggregate the full detail view for one job.

    Args:
        job_id: Store-assigned job identity (numeric).
        service: Shared jobs view service from the app state.

    Returns:
        Job, evaluation blocks, status + history + notes, twins, interview
        rounds, and application snapshot refs.

    Raises:
        HTTPException: 404 (shared error shape) when the job is unknown.
    """
    detail = await service.get_job_detail(str(job_id))
    if detail is None:
        raise HTTPException(
            status_code=_HTTP_NOT_FOUND, detail=f"job {job_id} not found"
        )
    response = job_detail_response(detail)
    if not hasattr(type(store), "resolve_real_job_id"):
        return response
    canonical = cast(_CanonicalStatusPort, store)
    try:
        real_id = await canonical.resolve_real_job_id(str(job_id))
    except ValueError:
        # Keep a saved source link readable while an orphan is under review.
        return response
    if real_id is not None:
        response.real_job_id = real_id
        status = await canonical.get_real_job_status(real_id)
        if status is not None:
            response.status.status = status.status
            response.status.decision = decision_for_status(status.status)
            response.status.notes = status.notes
            response.status.next_followup_at = status.next_followup_at
            response.status.resume_variant = status.resume_variant
            response.status.history = await canonical.get_real_job_status_history(
                real_id
            )
        parent_view = await canonical.get_real_job_view(
            real_id,
            stage_a_policy=policy.stage_a(),
            stage_b_policy=policy.stage_b(),
        )
        if parent_view is not None:
            canonical_detail = real_job_detail_response(
                parent_view, history=[], interviews=[]
            )
            response.evaluation = canonical_detail.evaluation
            response.evaluation_stale_reason = canonical_detail.evaluation_stale_reason
    return response


__all__ = ["router"]
