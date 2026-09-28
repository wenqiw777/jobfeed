"""Canonical Triage reads; path IDs never mean source-posting IDs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, Query

from jobfeed.cli import AppContext
from jobfeed.domain.interview import InterviewRound
from jobfeed.services._evaluate_canonical import _PolicySnapshot
from jobfeed.services.canonical_priority import (
    CanonicalPriorityInput,
    canonical_priority_rows,
)
from jobfeed.web.deps import get_context, get_current_scoring_policy, get_store
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.jobs_detail import interview_round_response
from jobfeed.web.schemas.real_jobs import (
    RealJobDetailResponse,
    RealJobsListParams,
    RealJobsListResponse,
    RealJobsSelectionParams,
    RealJobsSelectionResponse,
    real_job_detail_response,
    real_jobs_list_response,
)

router = APIRouter()


class RealJobViewsPort(Protocol):
    """Store capabilities required by canonical Triage HTTP routes."""

    async def query_real_jobs_view(self, **kwargs: object) -> dict[str, object]:
        """Read a filtered canonical list page and exact decision counts.

        Args:
            kwargs: Current decision, sort, search, and filtering arguments.

        Returns:
            Canonical cards, exact total, and counts for all decision tabs.
        """
        ...

    async def select_real_job_ids(self, **kwargs: object) -> dict[str, object]:
        """Capture all matching canonical IDs and count in one read transaction.

        Args:
            kwargs: Current decision, sort, search, and filtering arguments.

        Returns:
            One snapshot of matching parent IDs and its exact total.
        """
        ...

    async def get_real_job_view(
        self, real_job_id: str, **kwargs: object
    ) -> dict[str, object] | None:
        """Read a canonical parent with its representative posting and source evidence.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            kwargs: Current scoring-policy facts used to assess evaluation freshness.

        Returns:
            Representative row, sources, and identifiers; None if absent.
        """
        ...

    async def get_real_job_status_history(self, real_job_id: str) -> list[str]:
        """Read canonical destination statuses, most recent first.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Destination status names ordered newest first.
        """
        ...

    async def list_real_job_interviews(self, real_job_id: str) -> list[InterviewRound]:
        """List interview rounds for one canonical parent in round order.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.

        Returns:
            Canonical interview rounds in ascending round order.
        """
        ...

    async def load_real_job_priority_inputs(
        self, real_job_ids: list[str], **kwargs: object
    ) -> list[CanonicalPriorityInput]:
        """Load the inputs required for canonical priority projection.

        Args:
            real_job_ids: Canonical parent IDs to project.
            kwargs: Current scoring-policy facts for priority projection.

        Returns:
            Priority input rows for the requested parent IDs.
        """
        ...


def _store(store: Annotated[object, Depends(get_store)]) -> RealJobViewsPort:
    return cast(RealJobViewsPort, store)


_Store = Annotated[RealJobViewsPort, Depends(_store)]


@router.get("/real-jobs")
async def list_real_jobs(
    params: Annotated[RealJobsListParams, Query()],
    store: _Store,
    context: Annotated[AppContext, Depends(get_context)],
    policy: Annotated[_PolicySnapshot, Depends(get_current_scoring_policy)],
) -> RealJobsListResponse:
    """Return the Triage page with canonical priority projections.

    Args:
        params: Validated Triage query filters and page window.
        store: Canonical store resolved from the request context.
        context: Application settings and store context.
        policy: Current scoring-policy snapshot for stale-score projection.

    Returns:
        Triage cards with canonical priority and exact counts.
    """
    payload = await store.query_real_jobs_view(
        **params.model_dump(),
        hard_filters=context["settings"].hard_filters.to_domain(),
        stage_a_policy=policy.stage_a(),
        stage_b_policy=policy.stage_b(),
    )
    rows = payload["jobs"]
    assert isinstance(rows, list)
    inputs = await store.load_real_job_priority_inputs(
        [str(row["real_job_id"]) for row in rows],
        stage_a_policy=policy.stage_a(),
        stage_b_policy=policy.stage_b(),
    )
    priorities = {
        row.real_job_id: row.priority
        for row in canonical_priority_rows(inputs, now=datetime.now(UTC))
    }
    for row in rows:
        priority = priorities.get(str(row["real_job_id"]))
        row["priority_score"] = priority.priority_score if priority else None
        row["queue_tier"] = priority.queue_tier if priority else None
    return real_jobs_list_response(payload)


@router.get("/real-jobs/selection")
async def select_real_jobs(
    params: Annotated[RealJobsSelectionParams, Query()],
    store: _Store,
    context: Annotated[AppContext, Depends(get_context)],
    policy: Annotated[_PolicySnapshot, Depends(get_current_scoring_policy)],
) -> RealJobsSelectionResponse:
    """Return all currently matching canonical IDs for bulk selection.

    Args:
        params: Validated Triage query filters and page window.
        store: Canonical store resolved from the request context.
        context: Application settings and store context.
        policy: Current scoring-policy snapshot for stale-score projection.

    Returns:
        All matching canonical IDs with the exact snapshot count.
    """
    payload = await store.select_real_job_ids(
        **params.model_dump(),
        hard_filters=context["settings"].hard_filters.to_domain(),
        stage_a_policy=policy.stage_a(),
        stage_b_policy=policy.stage_b(),
    )
    return RealJobsSelectionResponse.model_validate(payload)


@router.get("/real-jobs/{real_job_id}")
async def get_real_job(
    real_job_id: int,
    store: _Store,
    policy: Annotated[_PolicySnapshot, Depends(get_current_scoring_policy)],
) -> RealJobDetailResponse:
    """Return canonical detail with source postings and status history.

    Args:
        real_job_id: Canonical parent ID, never a source posting ID.
        store: Canonical store resolved from the request context.
        policy: Current scoring-policy snapshot for stale-score projection.

    Returns:
        Canonical detail including sources, history, and interviews.

    Raises:
        ApiError: The canonical parent does not exist.
    """
    payload = await store.get_real_job_view(
        str(real_job_id),
        stage_a_policy=policy.stage_a(),
        stage_b_policy=policy.stage_b(),
    )
    if payload is None:
        raise ApiError(404, "not_found", f"real job {real_job_id} not found")
    history = await store.get_real_job_status_history(str(real_job_id))
    interviews = await store.list_real_job_interviews(str(real_job_id))
    return real_job_detail_response(
        payload,
        history=history,
        interviews=[interview_round_response(item) for item in interviews],
    )
