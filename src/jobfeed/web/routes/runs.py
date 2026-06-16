"""Pipeline runs routes: read, trigger, progress stream, active list."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import PipelineRun
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_views import StoreViewsMixin
from jobfeed.services.run_manager import RunManager
from jobfeed.web.deps import get_context, get_run_manager, get_store
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas import (
    RunsListResponse,
    RunSummary,
    run_summary,
    runs_list_response,
)

_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000
_SSE_HEARTBEAT_SECONDS = 15

router = APIRouter()

_Store = Annotated[JobStore, Depends(get_store)]
_Manager = Annotated[RunManager, Depends(get_run_manager)]


class TriggerScanRequest(BaseModel):
    """POST /api/runs/scan body."""

    source: str = "mock"


class TriggerEvaluateRequest(BaseModel):
    """POST /api/runs/evaluate body."""

    stage: Literal["a", "b", "both"] = "both"
    corpus: str = "unrated"
    limit: int | None = None


class _TriggerResponse(BaseModel):
    """Response body for a successfully triggered run."""

    run_id: str
    status: str = "running"


# ---------------------------------------------------------------------------
# Read-only routes
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_runs(
    store: _Store,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunsListResponse:
    """List pipeline runs, newest first, with the all-time total.

    Args:
        store: Shared job store.
        limit: Max runs returned.
        offset: Runs to skip.

    Returns:
        Runs window plus the total.
    """
    runs, total = await cast(StoreViewsMixin, store).list_pipeline_runs(
        limit=limit, offset=offset
    )
    return runs_list_response(runs, total)


@router.get("/runs/active")
async def get_active_runs(
    run_manager: _Manager,
) -> dict[str, list[dict[str, object]]]:
    """Return all currently active (in-progress) runs.

    Args:
        run_manager: Shared run manager.

    Returns:
        Active runs keyed under ``runs``.
    """
    active = run_manager.get_active_runs()
    return {
        "runs": [
            {
                "run_id": ar.run_id,
                "source": ar.source,
                "started_at": ar.started_at.isoformat(),
                "counters": run_summary(ar.run).model_dump(),
            }
            for ar in active
        ]
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, store: _Store) -> RunSummary:
    """Load one pipeline run by identity.

    Args:
        run_id: Run identity.
        store: Shared job store.

    Returns:
        The run's counters-only summary.

    Raises:
        ApiError: 404 when the run is unknown.
    """
    run = await store.get_pipeline_run(run_id)
    if run is None:
        raise ApiError(_HTTP_NOT_FOUND, "not_found", f"run {run_id} not found")
    return run_summary(run)


# ---------------------------------------------------------------------------
# Trigger routes
# ---------------------------------------------------------------------------


@router.post("/runs/scan")
async def trigger_scan(
    body: TriggerScanRequest,
    run_manager: _Manager,
    request: Request,
) -> _TriggerResponse:
    """Trigger a background scan run.

    Args:
        body: Request body with source name.
        run_manager: Shared run manager.
        request: Current request (for context access).

    Returns:
        The new run's identity and initial status.

    Raises:
        ApiError: 409 when a scan is already running, 400 for unknown source.
    """
    context = get_context(request)
    source = context["sources"].get(body.source)
    if source is None:
        raise ApiError(
            _HTTP_BAD_REQUEST,
            "unknown_source",
            f"source {body.source!r} not found",
        )
    try:
        run_id = await run_manager.trigger_scan([(body.source, source, {})])
    except RunConflictError as exc:
        raise ApiError(_HTTP_CONFLICT, "scan_already_running", str(exc)) from exc
    return _TriggerResponse(run_id=run_id)


@router.post("/runs/evaluate")
async def trigger_evaluate(
    body: TriggerEvaluateRequest, run_manager: _Manager
) -> _TriggerResponse:
    """Trigger a background evaluate run.

    Args:
        body: Request body with stage, corpus, limit.
        run_manager: Shared run manager.

    Returns:
        The new run's identity and initial status.

    Raises:
        ApiError: 409 when an evaluate is already running.
    """
    try:
        run_id = await run_manager.trigger_evaluate(
            stage=body.stage,
            corpus=body.corpus,
            limit=body.limit,
        )
    except RunConflictError as exc:
        raise ApiError(_HTTP_CONFLICT, "evaluate_already_running", str(exc)) from exc
    return _TriggerResponse(run_id=run_id)


# ---------------------------------------------------------------------------
# SSE progress stream
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/progress")
async def stream_progress(
    run_id: str, run_manager: _Manager, store: _Store
) -> StreamingResponse:
    """Stream progress events for a run as Server-Sent Events.

    Args:
        run_id: Run identity to stream.
        run_manager: Shared run manager.
        store: Shared job store for checking finished runs.

    Returns:
        SSE streaming response.

    Raises:
        ApiError: 404 when the run is unknown.
    """
    active_ids = {ar.run_id for ar in run_manager.get_active_runs()}
    if run_id not in active_ids:
        finished = await store.get_pipeline_run(run_id)
        if finished is None:
            raise ApiError(_HTTP_NOT_FOUND, "not_found", f"run {run_id} not found")
        return StreamingResponse(
            _done_generator(finished), media_type="text/event-stream"
        )
    return StreamingResponse(
        _stream_progress(run_manager, run_id),
        media_type="text/event-stream",
    )


async def _done_generator(run: PipelineRun) -> AsyncIterator[str]:
    """Yield a single ``event: done`` SSE frame for a finished run."""
    summary = run_summary(run).model_dump(mode="json")
    yield f"event: done\ndata: {json.dumps(summary)}\n\n"


async def _stream_progress(run_manager: RunManager, run_id: str) -> AsyncIterator[str]:
    """Yield SSE data frames plus heartbeats while a run progresses.

    Heartbeats (``: heartbeat``) fire every 15 s to keep alive. A final
    ``event: done`` is emitted when the run completes.
    """
    last_run: PipelineRun | None = None
    async for item in _subscribe_with_heartbeat(run_manager, run_id):
        if item is None:
            yield ": heartbeat\n\n"
        else:
            last_run = item
            summary = run_summary(item).model_dump(mode="json")
            yield f"data: {json.dumps(summary)}\n\n"
    if last_run is not None:
        summary = run_summary(last_run).model_dump(mode="json")
        yield f"event: done\ndata: {json.dumps(summary)}\n\n"
    else:
        yield "event: done\ndata: {}\n\n"


async def _subscribe_with_heartbeat(
    run_manager: RunManager, run_id: str
) -> AsyncIterator[PipelineRun | None]:
    """Merge run progress events with periodic heartbeat signals.

    Yields PipelineRun on progress and None on heartbeat timeouts.
    """
    subscriber = run_manager.subscribe(run_id)
    try:
        while True:
            try:
                run = await asyncio.wait_for(
                    subscriber.__anext__(), timeout=_SSE_HEARTBEAT_SECONDS
                )
                yield run
            except TimeoutError:
                yield None
            except StopAsyncIteration:
                break
    finally:
        await subscriber.aclose()


__all__ = ["router"]
