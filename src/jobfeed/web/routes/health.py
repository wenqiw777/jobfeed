"""Health route: process liveness plus a database roundtrip."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from jobfeed.observability import get_logger
from jobfeed.ports.store import JobStore
from jobfeed.web.deps import get_store

_HTTP_SERVICE_UNAVAILABLE = 503

router = APIRouter()


@router.get("/health")
async def get_health(
    request: Request, store: Annotated[JobStore, Depends(get_store)]
) -> JSONResponse:
    """Report process liveness and database reachability.

    Args:
        store: Shared job store from the app state.

    Returns:
        200 ``{"status": "ok", "db": "ok"}`` when a cheap store read
        succeeds, else 503 with a degraded body.
    """
    try:
        # LIMIT 0 still performs a real database roundtrip without sorting or
        # hydrating a row from the large jobs/JD table.
        await store.list_jobs(limit=0)
    except Exception as exc:
        get_logger().error("health_db_error", error=str(exc))
        return JSONResponse(
            status_code=_HTTP_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "db": "error"},
        )
    settings = request.app.state.context["settings"].redis_pipeline
    if settings.enabled:
        try:
            async with Redis.from_url(
                settings.url, socket_connect_timeout=1, socket_timeout=1
            ) as redis:
                await redis.ping()
        except Exception:
            get_logger().error("health_redis_error")
            return JSONResponse(
                status_code=_HTTP_SERVICE_UNAVAILABLE,
                content={"status": "degraded", "db": "ok", "redis": "error"},
            )
        return JSONResponse(content={"status": "ok", "db": "ok", "redis": "ok"})
    return JSONResponse(content={"status": "ok", "db": "ok"})


__all__ = ["router"]
