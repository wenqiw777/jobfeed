"""Read-only Results demo on the fixed benchmark database; no scan workers."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.config import load_settings
from jobfeed.config_editor import EditableConfiguration
from jobfeed.services.jobs_view import JobsViewService
from jobfeed.web.routes.jobs import router
from jobfeed.web.schemas.configuration import configuration_response


@asynccontextmanager
async def lifespan(app):
    store = SQLiteStore(Path("artifacts/results-performance/fixed.sqlite"))
    await store.connect()
    app.state.jobs_view_service = JobsViewService(
        store, load_settings(Path("config.toml")).hard_filters.to_domain()
    )
    try:
        yield
    finally:
        await store.close()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def read_only(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse({"detail": "Read-only performance demo"}, status_code=403)
    return await call_next(request)


app.include_router(router, prefix="/api")
app.mount("/assets", StaticFiles(directory="web-ui/dist/assets"), name="assets")


@app.get("/api/config")
async def configuration():
    settings = load_settings(Path("config.toml"))
    return configuration_response(
        EditableConfiguration.from_settings(settings), configured=True
    )


@app.get("/triage")
async def triage():
    return FileResponse("web-ui/dist/index.html")
