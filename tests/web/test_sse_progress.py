"""Tests for GET /api/runs/{run_id}/progress SSE streaming.

Uses a mock RunManager to control the subscribe iterator without real
services or Postgres.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from jobfeed.domain.models import PipelineRun
from jobfeed.services.run_manager import RUN_DONE, ActiveRun
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import FakeStore, fake_context, open_client

HTTP_OK = 200
HTTP_NOT_FOUND = 404
_DISCOVERED_MID = 5
_DISCOVERED_FINAL = 10
_INSERTED_FINAL = 3
_EXPECTED_DATA_EVENTS = 2


def _make_run(
    run_id: str = "run-1",
    source: str = "mock",
    status: str = "running",
    finished_at: datetime | None = None,
) -> PipelineRun:
    """Build a minimal PipelineRun fixture."""
    return PipelineRun(
        run_id=run_id,
        started_at=datetime.now(UTC),
        source=source,
        status=status,
        finished_at=finished_at,
    )


class SSERunManager:
    """RunManager stand-in with controlled subscribe behavior."""

    def __init__(self) -> None:
        self._active: list[ActiveRun] = []
        self._events: dict[str, list[PipelineRun]] = {}

    def add_active(self, run: PipelineRun) -> None:
        """Register a run as active with pre-configured progress events."""
        self._active.append(
            ActiveRun(
                run_id=run.run_id,
                source=run.source,
                started_at=run.started_at,
                run=run,
            )
        )

    def set_events(self, run_id: str, events: list[PipelineRun]) -> None:
        """Set the progress events that subscribe will yield."""
        self._events[run_id] = events

    def get_active_runs(self) -> list[ActiveRun]:
        """Return configured active runs."""
        return list(self._active)

    def subscribe(self, run_id: str) -> asyncio.Queue[PipelineRun | object]:
        """Return a queue pre-filled with progress events plus RUN_DONE."""
        queue: asyncio.Queue[PipelineRun | object] = asyncio.Queue()
        for event in self._events.get(run_id, []):
            queue.put_nowait(event)
        queue.put_nowait(RUN_DONE)
        return queue

    def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[PipelineRun | object]
    ) -> None:
        """No-op unsubscribe."""

    async def recover_stale_runs(self) -> int:
        """No-op recovery."""
        return 0


class ProgressFakeStore(FakeStore):
    """FakeStore that can return a specific pipeline run by ID."""

    def __init__(self, runs: dict[str, PipelineRun] | None = None) -> None:
        super().__init__()
        self._runs = runs or {}

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Return a pre-configured run or None."""
        return self._runs.get(run_id)


def _build_app(
    run_manager: SSERunManager,
    store: ProgressFakeStore | None = None,
) -> Any:
    """Build a test app with the given RunManager and optional store."""
    st = store or ProgressFakeStore()
    ctx = fake_context(st)
    app = build_web_app(ctx)
    app.state.run_manager = run_manager
    return app


def _parse_sse(text: str) -> list[dict[str, str]]:
    """Parse SSE text into a list of events with type and data keys."""
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.split("\n"):
        if line.startswith("event: "):
            current["type"] = line[len("event: ") :]
        elif line.startswith("data: "):
            current["data"] = line[len("data: ") :]
        elif line.startswith(": "):
            current["type"] = "comment"
            current["data"] = line[2:]
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/progress
# ---------------------------------------------------------------------------


async def test_progress_unknown_run_returns_404() -> None:
    """GET /api/runs/{unknown}/progress returns 404."""
    mgr = SSERunManager()
    store = ProgressFakeStore()
    app = _build_app(mgr, store)

    async with open_client(app) as client:
        resp = await client.get("/api/runs/no-such-run/progress")

    assert resp.status_code == HTTP_NOT_FOUND
    assert resp.json()["error"]["code"] == "not_found"


async def test_progress_finished_run_returns_immediate_done() -> None:
    """GET /api/runs/{finished}/progress returns a single done event."""
    mgr = SSERunManager()
    finished = _make_run("run-done", status="succeeded", finished_at=datetime.now(UTC))
    store = ProgressFakeStore(runs={"run-done": finished})
    app = _build_app(mgr, store)

    async with open_client(app) as client:
        resp = await client.get("/api/runs/run-done/progress")

    assert resp.status_code == HTTP_OK
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) == 1
    data = json.loads(done_events[0]["data"])
    assert data["run_id"] == "run-done"
    assert data["status"] == "succeeded"


async def test_progress_active_run_streams_events() -> None:
    """GET /api/runs/{active}/progress streams data then done event."""
    mgr = SSERunManager()
    run = _make_run("run-active")
    mgr.add_active(run)

    # Two progress events: mid-run counters change
    event1 = _make_run("run-active")
    event1.jobs_discovered = _DISCOVERED_MID
    event2 = _make_run("run-active")
    event2.jobs_discovered = _DISCOVERED_FINAL
    event2.jobs_inserted = _INSERTED_FINAL
    mgr.set_events("run-active", [event1, event2])

    app = _build_app(mgr)

    async with open_client(app) as client:
        resp = await client.get("/api/runs/run-active/progress")

    assert resp.status_code == HTTP_OK
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)

    # Should have data events then a done event
    data_events = [e for e in events if "data" in e and e.get("type") != "done"]
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(data_events) == _EXPECTED_DATA_EVENTS
    assert len(done_events) == 1

    first = json.loads(data_events[0]["data"])
    assert first["jobs_discovered"] == _DISCOVERED_MID

    second = json.loads(data_events[1]["data"])
    assert second["jobs_discovered"] == _DISCOVERED_FINAL
    assert second["jobs_inserted"] == _INSERTED_FINAL

    done_data = json.loads(done_events[0]["data"])
    assert done_data["jobs_discovered"] == _DISCOVERED_FINAL


async def test_progress_late_subscriber_falls_back_to_store() -> None:
    """A stream that sees only RUN_DONE loads the final counters from the store.

    Covers the finish race: the run was active at the route check, but had
    already finished (and closed its subscribers) by the time the stream
    subscribed — the done event must still carry the persisted counters.
    """
    mgr = SSERunManager()
    run = _make_run("run-race")
    mgr.add_active(run)  # active at route-check time, but no events queued

    persisted = _make_run("run-race", status="succeeded", finished_at=datetime.now(UTC))
    persisted.jobs_discovered = _DISCOVERED_FINAL
    store = ProgressFakeStore(runs={"run-race": persisted})
    app = _build_app(mgr, store)

    async with open_client(app) as client:
        resp = await client.get("/api/runs/run-race/progress")

    assert resp.status_code == HTTP_OK
    events = _parse_sse(resp.text)
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) == 1
    data = json.loads(done_events[0]["data"])
    assert data["run_id"] == "run-race"
    assert data["jobs_discovered"] == _DISCOVERED_FINAL
