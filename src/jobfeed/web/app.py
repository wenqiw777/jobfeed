"""FastAPI application factory for the Jobfeed web API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from jobfeed.cli import AppContext, create_app
from jobfeed.web.errors import install_error_handling
from jobfeed.web.routes.health import router as health_router


def create_web_app(config_path: Path | None = None) -> FastAPI:
    """Build the web app from configuration, one assembly per process.

    Args:
        config_path: Optional path to the TOML configuration file.

    Returns:
        FastAPI app over a freshly assembled dependency graph.

    Raises:
        FileNotFoundError: If an explicit config path does not exist.
    """
    return build_web_app(create_app(config_path))


def build_web_app(context: AppContext) -> FastAPI:
    """Build the FastAPI app around an existing dependency graph.

    The context is constructed once per process and shared via ``app.state``;
    the lifespan only opens and closes the store connection, mirroring the
    CLI's ``run_with_store`` lifecycle.

    Args:
        context: Assembled dependency graph (store, services, settings).

    Returns:
        Configured FastAPI app serving the ``/api`` routes.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await context["store"].connect()
        try:
            yield
        finally:
            await context["store"].close()

    app = FastAPI(title="Jobfeed API", lifespan=lifespan)
    app.state.context = context
    install_error_handling(app)
    app.include_router(health_router, prefix="/api")
    return app


__all__ = ["build_web_app", "create_web_app"]
