"""Explicit historical application-route reconciliation over the live bridge."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from jobfeed.application_resolution_wiring import build_application_resolver
from jobfeed.domain.application_route import ApplicationRouteOutcome
from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import JobPosting, PipelineRun
from jobfeed.ports.application_resolution import ApplicationBackfillStore
from jobfeed.services.application_backfill import ApplicationBackfillService
from jobfeed.services.run_manager import RunManager
from jobfeed.services.run_orchestration import RunLeaseSession
from jobfeed.web.deps import get_context, get_run_manager
from jobfeed.web.errors import ApiError

router = APIRouter()


class ApplicationBackfillRequest(BaseModel):
    """All open candidates, or an explicit existing-ID / recency subset."""

    job_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/runs/application-backfill")
async def trigger_application_backfill(
    body: ApplicationBackfillRequest,
    request: Request,
    manager: Annotated[RunManager, Depends(get_run_manager)],
) -> dict[str, str]:
    """Start fenced identity work without enrichment or paid scoring.

    Args:
        body: Optional existing source IDs or posting recency window.
        request: Shared production store and connected Chrome bridge.
        manager: Run lifecycle and exclusive scan ownership.

    Returns:
        A stoppable run ID and initial status.

    Raises:
        ApiError: If the updated extension is unavailable or a scan is active.
    """
    context = get_context(request)
    bridge = context.get("jobright_bridge")
    if (
        bridge is None
        or not bridge.connected
        or "application-resolution" not in bridge.supported_sources
    ):
        raise ApiError(409, "extension_update_required", "Reload the Mini extension")
    store = cast(ApplicationBackfillStore, context["store"])
    resolver = build_application_resolver(bridge)

    async def resolve(job: JobPosting) -> ApplicationRouteOutcome:
        return await resolver.resolve(job, job.apply_url or job.url)

    async def work(
        session: RunLeaseSession, progress: Callable[[PipelineRun], None]
    ) -> None:
        since = datetime.now(UTC) - timedelta(days=body.days) if body.days else None
        ids = body.job_ids
        if ids is None:
            ids = await store.list_application_backfill_ids(since=since)
        await ApplicationBackfillService(store, resolve).run(
            ids, lease_session=session, on_progress=progress
        )

    try:
        run_id = await manager.trigger_application_backfill(work)
    except RunConflictError as exc:
        raise ApiError(409, "run_conflict", str(exc)) from exc
    return {"run_id": run_id, "status": "running"}
