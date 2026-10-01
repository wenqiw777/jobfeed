"""Scheduled foreground work may preempt backfill without retrying paid posts."""

import pytest

from scripts import jobfeed_scheduled_cycle as scheduler
from scripts.jobfeed_scheduled_cycle import run_cycle


class API:
    def __init__(self, active, statuses=None):
        self.active = iter(active)
        self.statuses = statuses or {"scan-1": "succeeded", "evaluate-1": "succeeded"}
        self.requests = []
        self.waits = []

    def request(self, path, payload=None):
        self.requests.append((path, payload))
        if path == "/api/runs/active":
            return {"runs": next(self.active)}
        if path == "/api/sources/jobright/status":
            return {"connected": True}
        return {"run_id": path.rsplit("/", 1)[-1] + "-1"}

    def wait(self, run_id):
        self.waits.append(run_id)
        return {"run_id": run_id, "status": self.statuses[run_id]}


@pytest.mark.parametrize(
    "active",
    [
        [[{"source": "application-backfill"}], []],
        [[], [{"source": "application-backfill"}]],
        [
            [{"source": "application-backfill"}],
            [{"source": "application-backfill"}],
        ],
    ],
)
def test_backfill_at_scan_or_evaluation_check_does_not_skip(active):
    api = API(active)
    assert run_cycle(api.request, api.wait) == "succeeded"
    assert [item for item in api.requests if item[1] is not None] == [
        ("/api/runs/scan", {"source": "all"}),
        (
            "/api/runs/evaluate",
            {"stage": "both", "scope": "backlog", "corpus": "unrated"},
        ),
    ]
    assert api.waits == ["scan-1", "evaluate-1"]


@pytest.mark.parametrize("source", ["all", "evaluate", "", None])
def test_other_active_work_still_blocks_initial_scan(source):
    api = API([[{"source": "application-backfill"}, {"source": source}]])
    assert run_cycle(api.request, api.wait) == "skipped_active"
    assert api.requests == [("/api/runs/active", None)]
    assert api.waits == []


def test_missing_source_active_work_still_blocks_initial_scan():
    api = API([[{"run_id": "unknown"}]])
    assert run_cycle(api.request, api.wait) == "skipped_active"
    assert api.waits == []


def test_other_work_starting_after_scan_blocks_evaluation():
    api = API([[], [{"source": "application-backfill"}, {"source": "evaluate"}]])
    assert run_cycle(api.request, api.wait) == "skipped_active"
    assert api.waits == ["scan-1"]
    assert [item for item in api.requests if item[1] is not None] == [
        ("/api/runs/scan", {"source": "all"})
    ]


@pytest.mark.parametrize("status", ["failed", "stopped", "partial", "cancelled"])
def test_unsuccessful_scan_never_starts_evaluation(status):
    api = API([[]], {"scan-1": status})
    with pytest.raises(RuntimeError, match=f"scan scan-1 {status}; cycle stopped"):
        run_cycle(api.request, api.wait)
    assert api.waits == ["scan-1"]
    assert [item for item in api.requests if item[1] is not None] == [
        ("/api/runs/scan", {"source": "all"})
    ]


def test_ambiguous_evaluation_post_is_not_retried():
    api = API([[], []])

    def request(path, payload=None):
        response = api.request(path, payload)
        if path == "/api/runs/evaluate":
            raise TimeoutError("response missing after evaluation accepted")
        return response

    with pytest.raises(TimeoutError, match="response missing"):
        run_cycle(request, api.wait)
    assert api.waits == ["scan-1"]
    assert [item for item in api.requests if item[0] == "/api/runs/evaluate"] == [
        (
            "/api/runs/evaluate",
            {"stage": "both", "scope": "backlog", "corpus": "unrated"},
        )
    ]


def test_wait_observes_only_the_exact_created_run(monkeypatch):
    calls, sleeps = [], []
    responses = iter([{"status": "running"}, {"status": "succeeded"}])
    clock = iter([0, 0, 10])

    def request(path):
        calls.append(path)
        return next(responses)

    monkeypatch.setattr(scheduler, "request_json", request)
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(scheduler.time, "sleep", sleeps.append)
    assert scheduler.wait_run("created-run-id") == {"status": "succeeded"}
    assert calls == ["/api/runs/created-run-id", "/api/runs/created-run-id"]
    assert sleeps == [10]


def test_observation_timeout_does_not_stop_or_retry_server_work(monkeypatch):
    calls = []
    clock = iter([0, 7201])
    monkeypatch.setattr(scheduler, "request_json", calls.append)
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="two-hour observation window"):
        scheduler.wait_run("created-run-id")
    assert calls == []
