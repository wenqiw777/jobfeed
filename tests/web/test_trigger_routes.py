"""Tests for POST /api/runs/scan, POST /api/runs/evaluate, GET /api/runs/active.

Uses a mock RunManager to verify the route logic without real services.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jobfeed.domain.errors import (
    LLMRuntimeUnavailable,
    ResumeNotConfiguredError,
    RunConflictError,
    SourceConfigError,
)
from jobfeed.domain.models import PipelineRun
from jobfeed.services.run_manager import ActiveRun
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import FakeStore, fake_context, open_client

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409
HTTP_SERVICE_UNAVAILABLE = 503
_EVAL_LIMIT = 10


def _make_run(run_id: str = "run-1", source: str = "mock") -> PipelineRun:
    """Build a minimal PipelineRun fixture."""
    return PipelineRun(
        run_id=run_id,
        started_at=datetime.now(UTC),
        source=source,
    )


class FakeRunManager:
    """In-memory RunManager stand-in for route-level testing."""

    def __init__(
        self,
        *,
        should_conflict_scan: bool = False,
        should_conflict_eval: bool = False,
        disabled_source: str | None = None,
        missing_resume: bool = False,
        unavailable_llm: bool = False,
    ) -> None:
        self._should_conflict_scan = should_conflict_scan
        self._should_conflict_eval = should_conflict_eval
        self._disabled_source = disabled_source
        self._missing_resume = missing_resume
        self._unavailable_llm = unavailable_llm
        self._active: list[ActiveRun] = []
        self.scan_calls: list[object] = []
        self.eval_calls: list[dict[str, object]] = []

    async def trigger_scan(self, source_name_or_specs: object) -> str:
        """Record the call and return a run id, or raise on conflict/config."""
        if self._should_conflict_scan:
            raise RunConflictError("A scan is already running")
        if source_name_or_specs == self._disabled_source:
            raise SourceConfigError(f"Source '{self._disabled_source}' is disabled")
        self.scan_calls.append(source_name_or_specs)
        return "run-scan-1"

    async def trigger_evaluate(self, **kwargs: Any) -> str:
        """Record the call and return a run id, or raise on conflict/config."""
        if self._should_conflict_eval:
            raise RunConflictError("An evaluation is already running")
        if self._missing_resume:
            raise ResumeNotConfiguredError("Resume file not found: /no/such/resume.md")
        if self._unavailable_llm:
            raise LLMRuntimeUnavailable(
                "codex-cli backend requires 'codex' to be installed and on PATH"
            )
        self.eval_calls.append(kwargs)
        return "run-eval-1"

    def get_active_runs(self) -> list[ActiveRun]:
        """Return pre-configured active runs."""
        return list(self._active)

    async def recover_stale_runs(self) -> int:
        """No-op recovery."""
        return 0


class RunSourcesFakeStore(FakeStore):
    """Store fake exposing one historical scan and its source counts."""

    def __init__(self) -> None:
        super().__init__()
        self.run = _make_run("scan-1", source="all")

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Return the configured run by ID."""
        return self.run if run_id == self.run.run_id else None

    async def get_new_job_source_counts(self, run_id: str) -> dict[str, int]:
        """Return exact configured-source counts for the known run."""
        return {"ats": 124, "indeed": 320} if run_id == self.run.run_id else {}


def _build_app(
    run_manager: FakeRunManager | None = None,
) -> tuple[Any, FakeRunManager]:
    """Build a test app with a fake RunManager wired in."""
    ctx = fake_context()
    app = build_web_app(ctx)
    mgr = run_manager or FakeRunManager()
    app.state.run_manager = mgr
    return app, mgr


async def test_run_new_job_sources_returns_exact_breakdown() -> None:
    """GET run sources returns counts whose total matches the new counter."""
    store = RunSourcesFakeStore()
    app = build_web_app(fake_context(store))
    app.state.run_manager = FakeRunManager()

    async with open_client(app) as client:
        response = await client.get("/api/runs/scan-1/new-job-sources")

    assert response.status_code == HTTP_OK
    assert response.json() == {
        "run_id": "scan-1",
        "source_counts": {"ats": 124, "indeed": 320},
        "total": 444,
    }


# ---------------------------------------------------------------------------
# POST /api/runs/scan
# ---------------------------------------------------------------------------


async def test_trigger_scan_returns_run_id() -> None:
    """POST /api/runs/scan returns 200 with run_id and running status."""
    app, mgr = _build_app()
    async with open_client(app) as client:
        resp = await client.post("/api/runs/scan", json={"source": "mock"})

    assert resp.status_code == HTTP_OK, resp.text
    body = resp.json()
    assert body["run_id"] == "run-scan-1"
    assert body["status"] == "running"
    assert len(mgr.scan_calls) == 1


async def test_trigger_scan_unknown_source_returns_400() -> None:
    """POST /api/runs/scan with an unknown source returns 400."""
    app, _mgr = _build_app()
    async with open_client(app) as client:
        resp = await client.post("/api/runs/scan", json={"source": "nonexistent"})

    assert resp.status_code == HTTP_BAD_REQUEST
    assert resp.json()["error"]["code"] == "unknown_source"


async def test_trigger_scan_disabled_source_returns_400() -> None:
    """POST /api/runs/scan with a known-but-disabled source returns 400."""
    app, _mgr = _build_app(FakeRunManager(disabled_source="speedyapply"))
    async with open_client(app) as client:
        resp = await client.post("/api/runs/scan", json={"source": "speedyapply"})

    assert resp.status_code == HTTP_BAD_REQUEST
    assert resp.json()["error"]["code"] == "source_disabled"


async def test_trigger_scan_conflict_returns_409() -> None:
    """POST /api/runs/scan when already running returns 409."""
    app, _mgr = _build_app(FakeRunManager(should_conflict_scan=True))
    async with open_client(app) as client:
        resp = await client.post("/api/runs/scan", json={"source": "mock"})

    assert resp.status_code == HTTP_CONFLICT
    assert resp.json()["error"]["code"] == "scan_already_running"


# ---------------------------------------------------------------------------
# POST /api/runs/evaluate
# ---------------------------------------------------------------------------


async def test_trigger_evaluate_returns_run_id() -> None:
    """POST /api/runs/evaluate returns 200 with run_id."""
    app, mgr = _build_app()
    async with open_client(app) as client:
        resp = await client.post(
            "/api/runs/evaluate",
            json={"stage": "a", "corpus": "unrated", "limit": _EVAL_LIMIT},
        )

    assert resp.status_code == HTTP_OK, resp.text
    body = resp.json()
    assert body["run_id"] == "run-eval-1"
    assert body["status"] == "running"
    assert len(mgr.eval_calls) == 1
    assert mgr.eval_calls[0]["stage"] == "a"
    assert mgr.eval_calls[0]["limit"] == _EVAL_LIMIT


async def test_trigger_evaluate_defaults() -> None:
    """POST /api/runs/evaluate with empty body uses defaults."""
    app, mgr = _build_app()
    async with open_client(app) as client:
        resp = await client.post("/api/runs/evaluate", json={})

    assert resp.status_code == HTTP_OK
    assert mgr.eval_calls[0]["stage"] == "both"
    assert mgr.eval_calls[0]["corpus"] == "unrated"
    assert mgr.eval_calls[0]["limit"] is None


async def test_trigger_evaluate_missing_resume_returns_400() -> None:
    """POST /api/runs/evaluate maps a missing master resume to a clean 400."""
    app, _mgr = _build_app(FakeRunManager(missing_resume=True))
    async with open_client(app) as client:
        resp = await client.post("/api/runs/evaluate", json={})

    assert resp.status_code == HTTP_BAD_REQUEST
    body = resp.json()["error"]
    assert body["code"] == "resume_not_configured"
    assert "/no/such/resume.md" in body["message"]


async def test_trigger_evaluate_conflict_returns_409() -> None:
    """POST /api/runs/evaluate when already running returns 409."""
    app, _mgr = _build_app(FakeRunManager(should_conflict_eval=True))
    async with open_client(app) as client:
        resp = await client.post("/api/runs/evaluate", json={})

    assert resp.status_code == HTTP_CONFLICT
    assert resp.json()["error"]["code"] == "evaluate_already_running"


async def test_trigger_evaluate_unavailable_llm_returns_503() -> None:
    """POST /api/runs/evaluate maps missing backend runtime to a clean 503."""
    app, _mgr = _build_app(FakeRunManager(unavailable_llm=True))
    async with open_client(app) as client:
        resp = await client.post("/api/runs/evaluate", json={})

    assert resp.status_code == HTTP_SERVICE_UNAVAILABLE
    body = resp.json()["error"]
    assert body["code"] == "llm_runtime_unavailable"
    assert "codex-cli backend requires 'codex'" in body["message"]


# ---------------------------------------------------------------------------
# GET /api/runs/active
# ---------------------------------------------------------------------------


async def test_active_runs_empty() -> None:
    """GET /api/runs/active returns empty list when nothing is running."""
    app, _mgr = _build_app()
    async with open_client(app) as client:
        resp = await client.get("/api/runs/active")

    assert resp.status_code == HTTP_OK
    assert resp.json() == {"runs": []}


async def test_active_runs_with_entries() -> None:
    """GET /api/runs/active returns active runs with counters."""
    mgr = FakeRunManager()
    run = _make_run("run-42", source="scan")
    run.progress_stage = "stage_b"
    run.stage_a_total = 15
    run.stage_a_processed = 15
    run.stage_b_total = 4
    run.stage_b_processed = 2
    mgr._active = [
        ActiveRun(
            run_id="run-42",
            source="scan",
            started_at=run.started_at,
            run=run,
        )
    ]
    app, _ = _build_app(mgr)

    async with open_client(app) as client:
        resp = await client.get("/api/runs/active")

    assert resp.status_code == HTTP_OK
    body = resp.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["run_id"] == "run-42"
    assert body["runs"][0]["source"] == "scan"
    assert "counters" in body["runs"][0]
    assert body["runs"][0]["counters"]["progress_stage"] == "stage_b"
    assert body["runs"][0]["counters"]["stage_a_total"] == 15  # noqa: PLR2004
    assert body["runs"][0]["counters"]["stage_b_processed"] == 2  # noqa: PLR2004
