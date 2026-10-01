#!/usr/bin/env python3
"""Run one recent backfill, resuming only when foreground work preempts it."""

import argparse
import fcntl
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:7654"
ROOT = Path(__file__).resolve().parents[1]
_POLL_SECONDS = 10
_CONFLICT = 409
_MAX_DAYS = 3650


def request_json(path, payload=None):
    """Call loopback once; an unknown POST outcome is never retried."""
    request = urllib.request.Request(
        BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def scheduler_busy(path=ROOT / "data" / "scheduler" / "cycle.lock"):
    """Probe the scheduler's exclusive lock without retaining a lock for POST."""
    try:
        lock = path.open("r")
    except FileNotFoundError:
        return False
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
    return False


def _observe(request, run_id):
    active = request("/api/runs/active")["runs"]
    current = next((run for run in active if run.get("run_id") == run_id), None)
    if current is not None:
        counters = current.get("counters")
        if isinstance(counters, dict) and counters.get("status"):
            return counters
    return request(f"/api/runs/{run_id}")


def _wait_terminal(request, sleep, publish, run_id):
    """Observe only this attempt, preferring its active counters."""
    while True:
        run = _observe(request, run_id)
        status = run["status"]
        publish(
            "observing" if status == "running" else "terminal",
            status,
            run.get("failure_code"),
        )
        if status != "running":
            return run
        sleep(_POLL_SECONDS)


def _resume_allowed(request, run_ids, publish):
    """Recheck durable stop intent after idle and before each continuation POST."""
    if not run_ids:
        return True
    previous = request(f"/api/runs/{run_ids[-1]}")
    if (
        previous["status"] != "running"
        and previous.get("failure_code") == "foreground_priority"
    ):
        return True
    publish("terminal", previous["status"], previous.get("failure_code"))
    return False


def run_backfill(
    days=30,
    *,
    request=request_json,
    sleep=time.sleep,
    busy=scheduler_busy,
    record=lambda _state: None,
):
    """Wait for idle, observe exact attempts, and resume explicit priority stops.

    Args:
        days: Posting recency window, defaulting to thirty days.
        request: One-attempt JSON API request function.
        sleep: Poll delay function.
        busy: Non-blocking scheduler exclusive-lock probe.
        record: Observer receiving independent progress snapshots.

    Returns:
        Final continuation state; only succeeded is a successful completion.

    Raises:
        Exception: Network, ambiguous POST, or local persistence errors; no retry.
    """
    state = {
        "phase": "waiting_idle",
        "run_ids": [],
        "current_run_id": None,
        "status": "waiting",
        "failure_code": None,
    }

    def publish(phase, status, failure_code=None):
        state.update(phase=phase, status=status, failure_code=failure_code)
        record({**state, "run_ids": list(state["run_ids"])})

    try:
        while True:
            publish("waiting_idle", "waiting")
            while busy() or request("/api/runs/active")["runs"]:
                sleep(_POLL_SECONDS)
            if not _resume_allowed(request, state["run_ids"], publish):
                return state
            state["current_run_id"] = None
            publish("starting", "starting")
            try:
                accepted = request("/api/runs/application-backfill", {"days": days})
            except urllib.error.HTTPError as error:
                if error.code != _CONFLICT:
                    raise
                publish("waiting_conflict", "waiting")
                sleep(_POLL_SECONDS)
                continue
            run_id = accepted["run_id"]
            state["current_run_id"] = run_id
            state["run_ids"].append(run_id)
            publish("observing", "running")
            run = _wait_terminal(request, sleep, publish, run_id)
            if (
                run["status"] == "succeeded"
                or run.get("failure_code") != "foreground_priority"
            ):
                return state
    except KeyboardInterrupt:
        publish("interrupted", "stopped", "runner_interrupted")
        raise
    except Exception:
        publish("error", "failed", "runner_error")
        raise


def main():
    """Run the one-off continuation, with a local JSON observation file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "scheduler" / "continuation.json",
    )
    args = parser.parse_args()
    if not 1 <= args.days <= _MAX_DAYS:
        parser.error("--days must be between 1 and 3650")

    def record(state):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".tmp")
        temporary.write_text(json.dumps(state) + "\n", encoding="utf-8")
        temporary.replace(args.output)

    try:
        result = run_backfill(args.days, record=record)
    except Exception as error:
        print(f"Continuation stopped: {type(error).__name__}; no automatic retry")
        return 1
    print(json.dumps(result))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
