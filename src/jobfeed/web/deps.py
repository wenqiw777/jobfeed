"""Accessors for the per-process dependency graph on the web app state."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from jobfeed.cli import AppContext
from jobfeed.ports.store import JobStore


def get_context(request: Request) -> AppContext:
    """Return the per-process dependency graph stored on ``app.state``.

    The context is assembled once by the web app factory and shared by every
    request; handlers must never build stores or services themselves.

    Args:
        request: Current request.

    Returns:
        Shared application context.
    """
    return cast(AppContext, request.app.state.context)


def get_store(request: Request) -> JobStore:
    """Return the shared job store.

    Args:
        request: Current request.

    Returns:
        Job store whose connection is owned by the app lifespan.
    """
    return get_context(request)["store"]


__all__ = ["get_context", "get_store"]
