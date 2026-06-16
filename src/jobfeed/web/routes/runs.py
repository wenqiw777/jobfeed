"""Pipeline runs routes (read-only, thin parse/format shell)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query

from jobfeed.ports.store import JobStore
from jobfeed.ports.store_views import StoreViewsMixin
from jobfeed.web.deps import get_store
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas import (
    RunsListResponse,
    RunSummary,
    run_summary,
    runs_list_response,
)

_HTTP_NOT_FOUND = 404
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000

router = APIRouter()

_Store = Annotated[JobStore, Depends(get_store)]


@router.get("/runs")
async def list_runs(
    store: _Store,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunsListResponse:
    """List pipeline runs, newest first, with the all-time total.

    Args:
        store: Shared job store from the app state.
        limit: Maximum runs returned.
        offset: Runs to skip before the returned window.

    Returns:
        Runs window (started_at DESC, run_id DESC tiebreak) plus the total.
    """
    runs, total = await cast(StoreViewsMixin, store).list_pipeline_runs(
        limit=limit, offset=offset
    )
    return runs_list_response(runs, total)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, store: _Store) -> RunSummary:
    """Load one pipeline run's counters by identity.

    Args:
        run_id: Run identity.
        store: Shared job store from the app state.

    Returns:
        The run's counters-only summary.

    Raises:
        ApiError: 404 (shared error shape) when the run is unknown.
    """
    run = await store.get_pipeline_run(run_id)
    if run is None:
        raise ApiError(_HTTP_NOT_FOUND, "not_found", f"run {run_id} not found")
    return run_summary(run)


__all__ = ["router"]
