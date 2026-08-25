"""Historical scan source-attribution routes."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from jobfeed.ports.store import JobStore
from jobfeed.ports.store_views import StoreViewsMixin
from jobfeed.web.deps import get_store
from jobfeed.web.errors import ApiError

_HTTP_NOT_FOUND = 404

router = APIRouter()

_Store = Annotated[JobStore, Depends(get_store)]


class RunNewJobSourcesResponse(BaseModel):
    """Exact first-insert counts by user-configured scan source."""

    run_id: str
    source_counts: dict[str, int]
    total: int


@router.get("/runs/{run_id}/new-job-sources")
async def get_run_new_job_sources(
    run_id: str,
    store: _Store,
) -> RunNewJobSourcesResponse:
    """Return configured-source attribution for a scan's new jobs.

    Args:
        run_id: Historical or active pipeline run identity.
        store: Shared job store.

    Returns:
        Exact first-insert counts by configured source and their total.

    Raises:
        ApiError: 404 when the run is unknown.
    """
    run = await store.get_pipeline_run(run_id)
    if run is None:
        raise ApiError(_HTTP_NOT_FOUND, "not_found", f"run {run_id} not found")
    counts = await cast(StoreViewsMixin, store).get_new_job_source_counts(run_id)
    return RunNewJobSourcesResponse(
        run_id=run_id,
        source_counts=counts,
        total=sum(counts.values()),
    )
