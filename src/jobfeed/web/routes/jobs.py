"""Jobs routes: list view and detail aggregation (thin parse/format shell).

All composition (hard filters, fold, requested sort, pagination) lives in
``services/jobs_view.py``; these handlers only parse parameters, call the
service, and render DTOs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from jobfeed.services.jobs_view import JobsViewService
from jobfeed.web.deps import get_jobs_view_service
from jobfeed.web.schemas import (
    JobDetailResponse,
    JobsListParams,
    JobsListResponse,
    job_detail_response,
    jobs_list_response,
)

_HTTP_NOT_FOUND = 404

router = APIRouter()


@router.get("/jobs")
async def list_jobs(
    params: Annotated[JobsListParams, Query()],
    service: Annotated[JobsViewService, Depends(get_jobs_view_service)],
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
    page = await service.list_jobs(
        params.to_query(),
        apply_hard_filters=params.apply_hard_filters,
        dedupe=params.dedupe,
        sort=params.sort,
    )
    return jobs_list_response(page)


@router.get("/jobs/{job_id}")
async def get_job_detail(
    job_id: int,
    service: Annotated[JobsViewService, Depends(get_jobs_view_service)],
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
    return job_detail_response(detail)


__all__ = ["router"]
