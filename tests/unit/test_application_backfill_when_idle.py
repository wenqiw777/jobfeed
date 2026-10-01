"""One-off backfill continuation resumes only explicit foreground preemption."""

import fcntl
import json
from urllib.error import HTTPError

import pytest

from scripts import application_backfill_when_idle as runner
from scripts.application_backfill_when_idle import run_backfill, scheduler_busy


class API:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def request(self, path, payload=None):
        self.calls.append((path, payload))
        expected, result = self.steps.pop(0)
        assert path == expected
        if path == "/api/runs/application-backfill":
            assert payload == {"days": 30}
        else:
            assert payload is None
        if isinstance(result, Exception):
            raise result
        return result


def test_priority_preemption_waits_for_foreground_and_scheduler_then_resumes():
    api = API(
        [
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/application-backfill", {"run_id": "first"}),
            (
                "/api/runs/active",
                {"runs": [{"run_id": "first", "counters": {"status": "running"}}]},
            ),
            ("/api/runs/active", {"runs": []}),
            (
                "/api/runs/first",
                {"status": "stopped", "failure_code": "foreground_priority"},
            ),
            (
                "/api/runs/active",
                {"runs": [{"run_id": "foreground", "source": "all"}]},
            ),
            ("/api/runs/active", {"runs": []}),
            (
                "/api/runs/first",
                {"status": "stopped", "failure_code": "foreground_priority"},
            ),
            ("/api/runs/application-backfill", {"run_id": "second"}),
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/second", {"status": "succeeded"}),
        ]
    )
    busy = iter([False, False, True, False])
    sleeps, records = [], []
    result = run_backfill(
        request=api.request,
        sleep=sleeps.append,
        busy=lambda: next(busy),
        record=records.append,
    )
    assert result["status"] == "succeeded"
    assert result["phase"] == "terminal"
    assert result["run_ids"] == ["first", "second"]
    assert result["current_run_id"] == "second"
    assert records[0]["run_ids"] == []
    assert any(record["failure_code"] == "foreground_priority" for record in records)
    assert sleeps == [10, 10, 10]
    assert api.steps == []


@pytest.mark.parametrize(
    "terminal",
    [
        {"status": "stopped", "failure_code": "user_stopped"},
        {"status": "failed", "failure_code": "interrupted"},
    ],
)
def test_late_stop_or_shutdown_while_waiting_idle_prevents_restart(terminal):
    api = API(
        [
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/application-backfill", {"run_id": "first"}),
            ("/api/runs/active", {"runs": []}),
            (
                "/api/runs/first",
                {"status": "stopped", "failure_code": "foreground_priority"},
            ),
            (
                "/api/runs/active",
                {"runs": [{"run_id": "foreground", "source": "all"}]},
            ),
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/first", terminal),
        ]
    )
    records, sleeps = [], []
    result = run_backfill(
        request=api.request,
        busy=lambda: False,
        sleep=sleeps.append,
        record=records.append,
    )
    assert result["phase"] == "terminal"
    assert result["status"] == terminal["status"]
    assert result["failure_code"] == terminal["failure_code"]
    assert result["current_run_id"] == "first"
    assert result["run_ids"] == ["first"]
    assert records[-1] == result
    assert sleeps == [10]
    assert api.steps == []
    assert [path for path, payload in api.calls if payload is not None] == [
        "/api/runs/application-backfill"
    ]


@pytest.mark.parametrize(
    "failure_code", ["user_stopped", "interrupted", "runtime_error", None]
)
def test_manual_stop_and_other_failures_do_not_restart(failure_code):
    api = API(
        [
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/application-backfill", {"run_id": "only"}),
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/only", {"status": "stopped", "failure_code": failure_code}),
        ]
    )
    records = []
    result = run_backfill(
        request=api.request, busy=lambda: False, record=records.append
    )
    assert result["status"] == "stopped"
    assert result["run_ids"] == ["only"]
    assert result["failure_code"] == failure_code
    assert api.steps == []


def test_explicit_409_not_accepted_can_wait_and_retry():
    api = API(
        [
            ("/api/runs/active", {"runs": []}),
            (
                "/api/runs/application-backfill",
                HTTPError("http://127.0.0.1/", 409, "Conflict", None, None),
            ),
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/application-backfill", {"run_id": "accepted"}),
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/accepted", {"status": "succeeded"}),
        ]
    )
    sleeps, records = [], []
    result = run_backfill(
        request=api.request,
        busy=lambda: False,
        sleep=sleeps.append,
        record=records.append,
    )
    assert result["run_ids"] == ["accepted"]
    assert sleeps == [10]
    assert any(record["phase"] == "waiting_conflict" for record in records)
    assert api.steps == []


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("ambiguous POST"),
        HTTPError("http://127.0.0.1/", 500, "Error", None, None),
    ],
)
def test_ambiguous_or_other_failed_post_is_not_retried(error):
    api = API(
        [
            ("/api/runs/active", {"runs": []}),
            ("/api/runs/application-backfill", error),
        ]
    )
    records = []
    with pytest.raises(type(error)):
        run_backfill(request=api.request, busy=lambda: False, record=records.append)
    assert records[-1]["phase"] == "error"
    assert records[-1]["status"] == "failed"
    assert records[-1]["run_ids"] == []
    assert api.steps == []


def test_scheduler_lock_probe_releases_shared_lock_and_never_creates_file(tmp_path):
    path = tmp_path / "cycle.lock"
    assert not scheduler_busy(path)
    assert not path.exists()
    with path.open("w") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert scheduler_busy(path)
        fcntl.flock(owner, fcntl.LOCK_UN)
        assert not scheduler_busy(path)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(owner, fcntl.LOCK_UN)


def test_cli_default_recency_and_output_are_explicit_without_scheduler_changes(
    monkeypatch, tmp_path
):
    output = tmp_path / "observations" / "continuation.json"
    state = {
        "phase": "terminal",
        "status": "succeeded",
        "current_run_id": "only",
        "run_ids": ["only"],
    }
    calls = []

    def run(days, *, record):
        calls.append(days)
        record(state)
        return state

    monkeypatch.setattr(runner, "run_backfill", run)
    monkeypatch.setattr("sys.argv", ["runner", "--output", str(output)])
    assert runner.main() == 0
    assert calls == [30]
    assert json.loads(output.read_text()) == state
    assert not output.with_name(output.name + ".tmp").exists()
