"""Health route: process liveness plus a database roundtrip."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from jobfeed.observability import get_logger
from jobfeed.ports.store import JobStore
from jobfeed.web.deps import get_store

_HTTP_SERVICE_UNAVAILABLE = 503

router = APIRouter()


@router.get("/health")
async def get_health(store: Annotated[JobStore, Depends(get_store)]) -> JSONResponse:
    """Report process liveness and database reachability.

    Args:
        store: Shared job store from the app state.

    Returns:
        200 ``{"status": "ok", "db": "ok"}`` when a cheap store read
        succeeds, else 503 with a degraded body.
    """
    try:
        await store.list_jobs(limit=1)
    except Exception as exc:
        get_logger().error("health_db_error", error=str(exc))
        return JSONResponse(
            status_code=_HTTP_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "db": "error"},
        )
    return JSONResponse(content={"status": "ok", "db": "ok"})


__all__ = ["router"]
