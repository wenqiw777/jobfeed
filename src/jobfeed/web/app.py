"""FastAPI application factory for the Jobfeed web API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from jobfeed.cli import AppContext, create_app
from jobfeed.observability import get_logger
from jobfeed.services.application import ApplicationService, ApplicationStore
from jobfeed.services.jobs_view import JobsViewService, JobsViewStore
from jobfeed.services.workflow import WorkflowService, WorkflowStore
from jobfeed.web.errors import install_error_handling
from jobfeed.web.routes.applications import router as applications_router
from jobfeed.web.routes.health import router as health_router
from jobfeed.web.routes.jobs import router as jobs_router
from jobfeed.web.routes.workflow import router as workflow_router


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
    app.state.jobs_view_service = JobsViewService(
        store=cast(JobsViewStore, context["store"]),
        hard_filters=context["settings"].hard_filters.to_domain(),
    )
    # get_logger() is the same instance create_app wires into the context;
    # using it directly keeps the factory's build-time context needs minimal.
    logger = get_logger()
    app.state.workflow_service = WorkflowService(
        cast(WorkflowStore, context["store"]), logger
    )
    app.state.application_service = ApplicationService(
        cast(ApplicationStore, context["store"]), logger
    )
    install_error_handling(app)
    app.include_router(health_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(workflow_router, prefix="/api")
    app.include_router(applications_router, prefix="/api")
    return app


__all__ = ["build_web_app", "create_web_app"]
