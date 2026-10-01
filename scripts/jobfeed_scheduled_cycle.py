#!/usr/bin/env python3
"""Mini-local scan then unrated evaluation, through the existing server API."""

import argparse
import fcntl
import json
import logging
import logging.handlers
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:7654"
ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("jobfeed.schedule")


def request_json(path, payload=None):
    """Call loopback once; never retry an ambiguous POST and duplicate paid work."""
    request = urllib.request.Request(
        BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def wait_run(run_id):
    """Observe the exact run; timeout leaves existing server work untouched."""
    deadline = time.monotonic() + 7200
    while time.monotonic() < deadline:
        run = request_json(f"/api/runs/{run_id}")
        if run["status"] != "running":
            return run
        time.sleep(10)
    raise TimeoutError(f"Run {run_id} exceeded two-hour observation window")


def run_cycle(request=request_json, wait=wait_run):
    """Skip foreground work; API preempts backfill before scan or evaluation."""
    if _has_foreground_run(request("/api/runs/active")["runs"]):
        LOG.info("Skipped: another run is active")
        return "skipped_active"
    if not request("/api/sources/jobright/status")["connected"]:
        raise RuntimeError("Chrome extension is disconnected; scan not started")
    for phase, payload in (
        ("scan", {"source": "all"}),
        ("evaluate", {"stage": "both", "scope": "backlog", "corpus": "unrated"}),
    ):
        if phase == "evaluate" and _has_foreground_run(
            request("/api/runs/active")["runs"]
        ):
            LOG.info("Skipped evaluation: another run became active")
            return "skipped_active"
        run_id = request(f"/api/runs/{phase}", payload)["run_id"]
        LOG.info("Started %s run=%s", phase, run_id)
        result = wait(run_id)
        LOG.info(
            "Finished %s run=%s status=%s errors=%s cost=%s",
            phase,
            run_id,
            result["status"],
            result.get("errors"),
            result.get("total_llm_cost_usd"),
        )
        if result["status"] != "succeeded":
            raise RuntimeError(f"{phase} {run_id} {result['status']}; cycle stopped")
    return "succeeded"


def _has_foreground_run(runs):
    """Only recognized application backfill yields to scheduled foreground work."""
    return any(run.get("source") != "application-backfill" for run in runs)


def main():
    """Configure bounded logs and an OS-released lock for cron/launchd invocation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Read-only connectivity check"
    )
    args = parser.parse_args()
    if args.check:
        print(
            json.dumps(
                {
                    "active": request_json("/api/runs/active"),
                    "bridge": request_json("/api/sources/jobright/status"),
                }
            )
        )
        return 0
    directory = ROOT / "data" / "scheduler"
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        directory / "cycle.log",
        maxBytes=2_000_000,
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    with (directory / "cycle.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Skipped: scheduled cycle already running")
            return 0
        try:
            LOG.info("Cycle started")
            LOG.info("Cycle result=%s", run_cycle())
        except Exception:
            LOG.exception("Cycle failed; no automatic POST retry")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
