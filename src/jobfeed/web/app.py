"""FastAPI application factory for the Jobfeed web API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from jobfeed.cli import AppContext, create_app
from jobfeed.cli._evaluate_factory import EvalBuildParams, build_evaluate_service
from jobfeed.observability import get_logger, init_otel, init_sentry
from jobfeed.ports.store_perf import StorePerfMixin
from jobfeed.services.application import ApplicationService, ApplicationStore
from jobfeed.services.insights import InsightsService, InsightsStore
from jobfeed.services.jobs_view import JobsViewService, JobsViewStore
from jobfeed.services.performance import PerformanceService
from jobfeed.services.run_manager import RunManager
from jobfeed.services.scan import ScanService
from jobfeed.services.workflow import WorkflowService, WorkflowStore
from jobfeed.web.errors import install_error_handling
from jobfeed.web.routes.applications import router as applications_router
from jobfeed.web.routes.companies import router as companies_router
from jobfeed.web.routes.health import router as health_router
from jobfeed.web.routes.insights import router as insights_router
from jobfeed.web.routes.jobs import router as jobs_router
from jobfeed.web.routes.performance import router as performance_router
from jobfeed.web.routes.runs import router as runs_router
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


# Default location of the built SPA bundle, relative to the repo root
# (src/jobfeed/web/app.py -> repo root). Absent in API-only deployments.
_DEFAULT_DIST_DIR = Path(__file__).resolve().parents[3] / "web-ui" / "dist"


def build_web_app(context: AppContext, static_dir: Path | None = None) -> FastAPI:
    """Build the FastAPI app around an existing dependency graph.

    The context is constructed once per process and shared via ``app.state``;
    the lifespan only opens and closes the store connection, mirroring the
    CLI's ``run_with_store`` lifecycle.

    Args:
        context: Assembled dependency graph (store, services, settings).
        static_dir: SPA dist directory to serve at ``/``; defaults to the
            repo's ``web-ui/dist``. Ignored when no built bundle exists.

    Returns:
        Configured FastAPI app serving the ``/api`` routes, plus the SPA
        when a built bundle is present; ``app.state.is_spa_mounted`` records
        which of the two modes this process is in.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await context["store"].connect()
        try:
            if hasattr(_app.state, "run_manager"):
                await _app.state.run_manager.recover_stale_runs()
        except Exception:
            get_logger().warning("run_recovery_skipped")
        try:
            yield
        finally:
            await context["store"].close()

    init_otel(context["settings"].observability)
    init_sentry(context["settings"].observability)

    app = FastAPI(title="Jobfeed API", lifespan=lifespan)
    app.state.context = context
    # .get() tolerates minimal fake contexts that never exercise the probe.
    app.state.probe_company = context.get("probe_company")

    logger = get_logger()
    store = context["store"]
    app.state.run_manager = RunManager(
        store=store,
        logger=logger,
        scan_service_factory=lambda: ScanService(store, logger),
        evaluate_service_factory=lambda **kw: build_evaluate_service(
            context, EvalBuildParams(**kw)
        ),
    )

    app.state.jobs_view_service = JobsViewService(
        store=cast(JobsViewStore, context["store"]),
        hard_filters=context["settings"].hard_filters.to_domain(),
    )
    # get_logger() is the same instance create_app wires into the context;
    # using it directly keeps the factory's build-time context needs minimal.
    app.state.workflow_service = WorkflowService(
        cast(WorkflowStore, context["store"]), logger
    )
    application_service = ApplicationService(
        cast(ApplicationStore, context["store"]), logger
    )
    app.state.application_service = application_service
    app.state.insights_service = InsightsService(
        cast(InsightsStore, context["store"]), application_service
    )
    app.state.performance_service = PerformanceService(cast(StorePerfMixin, store))
    install_error_handling(app)
    app.include_router(health_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(workflow_router, prefix="/api")
    app.include_router(applications_router, prefix="/api")
    app.include_router(insights_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(companies_router, prefix="/api")
    app.include_router(performance_router, prefix="/api")
    dist_dir = static_dir if static_dir is not None else _DEFAULT_DIST_DIR
    is_spa_mounted = _mount_spa(app, dist_dir)
    app.state.is_spa_mounted = is_spa_mounted
    if is_spa_mounted:
        logger.info("web_ui_mounted", dist_dir=str(dist_dir))
    else:
        logger.info(
            "web_ui_not_built",
            dist_dir=str(dist_dir),
            hint="serving API only; run `make web-build` to build the UI",
        )
    return app


def _mount_spa(app: FastAPI, dist_dir: Path) -> bool:
    """Serve the built SPA from ``dist_dir`` with client-route fallback.

    No-op when no built bundle (``index.html``) exists, preserving the
    JSON-404-everywhere behavior of an API-only process. The catch-all is
    registered after the routers and excluded from the OpenAPI schema, so
    the committed snapshot is identical with and without a bundle. Unknown
    ``/api`` paths keep the JSON error shape: the catch-all re-raises a 404
    for them instead of serving HTML.

    Args:
        app: FastAPI app with the ``/api`` routers already included.
        dist_dir: Directory holding the Vite build output.

    Returns:
        True when a built bundle was found and mounted; False when the
        process stays API-only.
    """
    # Resolved so the no-cache check below also matches a direct
    # /index.html request, which _static_file_or_index resolves.
    index_file = (dist_dir / "index.html").resolve()
    if not index_file.is_file():
        return False
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        # StaticFiles gives hashed Vite assets correct content types and
        # JSON-shaped 404s on misses (an asset miss must not get HTML).
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.api_route("/{spa_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_spa(spa_path: str) -> FileResponse:
        """Serve a dist file when one matches, else the SPA index page.

        Registered for GET and HEAD; ``FileResponse`` answers HEAD with
        headers only.

        Args:
            spa_path: Request path relative to ``/``.

        Returns:
            File response for the matched dist file or ``index.html``. The
            index carries ``Cache-Control: no-cache`` so browsers revalidate
            the shell on every navigation instead of heuristically caching
            it; hashed ``/assets`` files keep their default caching.

        Raises:
            StarletteHTTPException: 404 for unknown ``/api`` paths and for
                ``assets/`` misses, which the shared handler renders in the
                JSON error shape.
        """
        if spa_path == "api" or spa_path.startswith("api/"):
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        if spa_path.startswith("assets/"):
            # Reached only when dist has no assets/ dir (no mount); an asset
            # miss must 404 rather than serve index.html as a script.
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        target = _static_file_or_index(dist_dir, index_file, spa_path)
        if target == index_file:
            return FileResponse(target, headers={"Cache-Control": "no-cache"})
        return FileResponse(target)

    return True


def _static_file_or_index(dist_dir: Path, index_file: Path, spa_path: str) -> Path:
    """Resolve the dist file for a request path, falling back to the index.

    Args:
        dist_dir: Directory holding the Vite build output.
        index_file: The SPA ``index.html`` inside ``dist_dir``.
        spa_path: Request path relative to ``/``.

    Returns:
        The matching file inside ``dist_dir``, or ``index_file`` for client
        router paths and for any path escaping the dist directory.
    """
    if not spa_path:
        return index_file
    candidate = (dist_dir / spa_path).resolve()
    if candidate.is_relative_to(dist_dir.resolve()) and candidate.is_file():
        return candidate
    return index_file


__all__ = ["build_web_app", "create_web_app"]
