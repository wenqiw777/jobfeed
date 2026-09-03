"""Pipeline runs routes: read, trigger, progress stream, active list."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from jobfeed.cli.scan import SOURCE_CHOICES
from jobfeed.domain.errors import (
    ResumeNotConfiguredError,
    RunConflictError,
    SourceConfigError,
)
from jobfeed.domain.models import PipelineRun
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_views import StoreViewsMixin
from jobfeed.services.run_manager import RUN_DONE, RunManager
from jobfeed.web.deps import get_run_manager, get_store
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
_MAX_WINDOW_DAYS = 365
_SSE_HEARTBEAT_SECONDS = 15
_PERSISTED_PROGRESS_POLL_SECONDS = 3

router = APIRouter()

_Store = Annotated[JobStore, Depends(get_store)]
_Manager = Annotated[RunManager, Depends(get_run_manager)]


class TriggerScanRequest(BaseModel):
    """POST /api/runs/scan body."""

    source: str = "mock"


class TriggerEvaluateRequest(BaseModel):
    """POST /api/runs/evaluate body."""

    stage: Literal["a", "b", "both"] = "both"
    scope: Literal["latest_scan", "backlog"] = "latest_scan"
    corpus: str = "unrated"
    limit: int | None = None


class _TriggerResponse(BaseModel):
    """Response body for a successfully triggered run."""

    run_id: str
    status: str = "running"


class _StopResponse(BaseModel):
    """Response after a run is stopped."""

    run_id: str
    status: str = "failed"


# ---------------------------------------------------------------------------
# Read-only routes
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_runs(
    store: _Store,
    run_manager: _Manager,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    days: Annotated[int | None, Query(ge=1, le=_MAX_WINDOW_DAYS)] = None,
) -> RunsListResponse:
    """List pipeline runs, newest first, with the matching total.

    Args:
        store: Shared job store.
        limit: Max runs returned.
        offset: Runs to skip.
        days: Optional look-back window; omit for all time.

    Returns:
        Runs window plus the total count matching ``days``.
    """
    await run_manager.recover_stale_runs()
    runs, total = await cast(StoreViewsMixin, store).list_pipeline_runs(
        limit=limit, offset=offset, days=days
    )
    return runs_list_response(runs, total)


@router.get("/runs/active")
async def get_active_runs(
    run_manager: _Manager,
    store: _Store,
) -> dict[str, list[dict[str, object]]]:
    """Return all currently active (in-progress) runs.

    Args:
        run_manager: Shared run manager.
        store: Persistent run store used for cross-process snapshots.

    Returns:
        Active runs keyed under ``runs``.
    """
    await run_manager.recover_stale_runs()
    active = run_manager.get_active_runs()
    active_ids = {item.run_id for item in active}
    persisted: list[PipelineRun] = []
    if hasattr(store, "list_pipeline_runs"):
        recent, _total = await cast(StoreViewsMixin, store).list_pipeline_runs(
            limit=_MAX_LIMIT,
            offset=0,
        )
        for run in recent:
            if run.status == "running" and run.run_id not in active_ids:
                persisted.append(await _persisted_live_snapshot(run, store))
    runs: list[dict[str, object]] = [
        {
            "run_id": ar.run_id,
            "source": ar.source,
            "started_at": ar.started_at.isoformat(),
            "counters": run_summary(ar.run).model_dump(),
        }
        for ar in active
    ]
    runs.extend(
        [
            {
                "run_id": run.run_id,
                "source": run.source,
                "started_at": run.started_at.isoformat(),
                "counters": run_summary(run).model_dump(),
            }
            for run in persisted
        ]
    )
    return {"runs": runs}


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


# Derived from the CLI source list (the UI dropdown is separate); linkedin
# (Playwright + cross-process lock) stays off the long-running web server.
_WEB_EXCLUDED_SOURCES = {"linkedin"}
_KNOWN_SOURCES = set(SOURCE_CHOICES) - _WEB_EXCLUDED_SOURCES


@router.post("/runs/scan")
async def trigger_scan(
    body: TriggerScanRequest,
    run_manager: _Manager,
) -> _TriggerResponse:
    """Trigger a background scan run.

    Args:
        body: Request body with source name.
        run_manager: Shared run manager.

    Returns:
        The new run's identity and initial status.

    Raises:
        ApiError: 409 when a scan is already running, 400 for an unknown
            or disabled source.
    """
    if body.source not in _KNOWN_SOURCES:
        raise ApiError(
            _HTTP_BAD_REQUEST,
            "unknown_source",
            f"source {body.source!r} not found",
        )
    try:
        run_id = await run_manager.trigger_scan(body.source)
    except RunConflictError as exc:
        raise ApiError(_HTTP_CONFLICT, "scan_already_running", str(exc)) from exc
    except SourceConfigError as exc:
        raise ApiError(_HTTP_BAD_REQUEST, "source_disabled", str(exc)) from exc
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
        ApiError: 409 when an evaluate is already running, 400 when the
            master resume file is not configured.
    """
    try:
        run_id = await run_manager.trigger_evaluate(
            stage=body.stage,
            scope=body.scope,
            corpus=body.corpus,
            limit=body.limit,
        )
    except RunConflictError as exc:
        raise ApiError(_HTTP_CONFLICT, "evaluate_already_running", str(exc)) from exc
    except ResumeNotConfiguredError as exc:
        # A missing master resume is a first-run user misconfiguration;
        # other missing files (ML model, price table) stay 500s.
        raise ApiError(_HTTP_BAD_REQUEST, "resume_not_configured", str(exc)) from exc
    return _TriggerResponse(run_id=run_id)


@router.post("/runs/{run_id}/stop")
async def stop_run(
    run_id: str,
    run_manager: _Manager,
    store: _Store,
) -> _StopResponse:
    """Stop a live run or clear a stale durable running row.

    Args:
        run_id: Pipeline run identity.
        run_manager: Shared run manager.
        store: Shared job store.

    Returns:
        Stopped run identity and terminal status.

    Raises:
        ApiError: When the run is unknown, terminal, or cannot be stopped.
    """
    run = await store.get_pipeline_run(run_id)
    if run is None:
        raise ApiError(_HTTP_NOT_FOUND, "not_found", f"run {run_id} not found")
    if run.status != "running":
        raise ApiError(_HTTP_CONFLICT, "run_not_running", "Run is not running")
    if not await run_manager.stop_run(run_id):
        raise ApiError(_HTTP_CONFLICT, "run_stop_failed", "Run could not be stopped")
    return _StopResponse(run_id=run_id)


@router.post("/runs/{run_id}/retry")
async def retry_run(
    run_id: str,
    run_manager: _Manager,
    store: _Store,
) -> _TriggerResponse:
    """Start a new run using the historical run's type and source.

    Args:
        run_id: Historical pipeline run identity.
        run_manager: Shared run manager.
        store: Shared job store.

    Returns:
        Newly triggered run identity and status.

    Raises:
        ApiError: When the source run is unknown, active, or cannot be retried.
    """
    run = await store.get_pipeline_run(run_id)
    if run is None:
        raise ApiError(_HTTP_NOT_FOUND, "not_found", f"run {run_id} not found")
    if run.status == "running":
        raise ApiError(
            _HTTP_CONFLICT,
            "run_still_running",
            "Stop the run before retrying",
        )
    if run.source == "evaluate":
        continue_failed = run.status == "succeeded" and run.errors > 0
        retry_job_ids = (
            await cast(StoreViewsMixin, store).list_retryable_run_error_job_ids(run_id)
            if continue_failed
            else None
        )
        if retry_job_ids == []:
            raise ApiError(
                _HTTP_CONFLICT,
                "no_retryable_errors",
                "No retryable errors remain for this run",
            )
        new_run_id = await run_manager.trigger_evaluate(
            stage=run.evaluate_stage or "both",
            scope="backlog" if continue_failed else "latest_scan",
            corpus="failed" if continue_failed else "unrated",
            limit=len(retry_job_ids) if retry_job_ids is not None else None,
            **({"job_ids": retry_job_ids} if retry_job_ids is not None else {}),
        )
        return _TriggerResponse(run_id=new_run_id)
    source = {"scan": "all", "linkedin_guest": "linkedin-guest"}.get(
        run.source, run.source
    )
    if source not in _KNOWN_SOURCES:
        raise ApiError(
            _HTTP_BAD_REQUEST,
            "unknown_source",
            f"source {source!r} cannot be retried",
        )
    new_run_id = await run_manager.trigger_scan(source)
    return _TriggerResponse(run_id=new_run_id)


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
        if finished.status == "running":
            return StreamingResponse(
                _poll_persisted_progress(run_id, store),
                media_type="text/event-stream",
            )
        return StreamingResponse(
            _done_generator(finished), media_type="text/event-stream"
        )
    return StreamingResponse(
        _stream_progress(run_manager, run_id, store),
        media_type="text/event-stream",
    )


async def _done_generator(run: PipelineRun) -> AsyncIterator[str]:
    """Yield a single ``event: done`` SSE frame for a finished run."""
    summary = run_summary(run).model_dump(mode="json")
    yield f"event: done\ndata: {json.dumps(summary)}\n\n"


async def _persisted_live_snapshot(run: PipelineRun, store: JobStore) -> PipelineRun:
    """Restore transient live fields from a durable checkpoint snapshot."""
    live = replace(run)
    live.progress_updated_at = run.last_progress_at
    if run.source == "evaluate":
        live.progress_stage = run.failed_stage or "preparing"
        live.stage_a_processed = run.stage_a_scored
        live.stage_b_processed = run.stage_b_scored
        progress_reader = getattr(store, "get_stage_b_run_progress", None)
        if live.progress_stage == "stage_b" and callable(progress_reader):
            processed, total = await progress_reader(run.run_id, run.started_at)
            live.stage_b_processed = processed
            live.stage_b_total = total
    else:
        live.scan_phase = run.failed_stage
        live.scan_source = run.failed_source
        live.scan_processed = run.jobs_discovered
    return live


async def _poll_persisted_progress(run_id: str, store: JobStore) -> AsyncIterator[str]:
    """Poll a live run owned by another process until it becomes terminal."""
    while True:
        run = await store.get_pipeline_run(run_id)
        if run is None:
            yield "event: done\ndata: {}\n\n"
            return
        live = await _persisted_live_snapshot(run, store)
        summary = run_summary(live).model_dump(mode="json")
        if run.status != "running":
            yield f"event: done\ndata: {json.dumps(summary)}\n\n"
            return
        yield f"data: {json.dumps(summary)}\n\n"
        await asyncio.sleep(_PERSISTED_PROGRESS_POLL_SECONDS)


async def _stream_progress(
    run_manager: RunManager, run_id: str, store: JobStore
) -> AsyncIterator[str]:
    """Yield SSE data frames plus heartbeats while a run progresses.

    Heartbeats (``: heartbeat``) fire every 15 s to keep alive. The timeout
    wraps a plain ``queue.get()`` — never the subscription itself — so an
    idle run keeps streaming instead of being unsubscribed by the first
    heartbeat. A final ``event: done`` is emitted when the run completes;
    a subscriber that joined after the finish loads the final counters
    from the store.
    """
    last_run: PipelineRun | None = None
    queue = run_manager.subscribe(run_id)
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=_SSE_HEARTBEAT_SECONDS
                )
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is RUN_DONE:
                break
            last_run = cast(PipelineRun, item)
            summary = run_summary(last_run).model_dump(mode="json")
            yield f"data: {json.dumps(summary)}\n\n"
    finally:
        run_manager.unsubscribe(run_id, queue)
    if last_run is None:
        last_run = await store.get_pipeline_run(run_id)
    if last_run is not None:
        summary = run_summary(last_run).model_dump(mode="json")
        yield f"event: done\ndata: {json.dumps(summary)}\n\n"
    else:
        yield "event: done\ndata: {}\n\n"


__all__ = ["router"]
