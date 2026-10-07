#!/usr/bin/env python3
"""Plan three daily random scan slots and run only their durable, dated triggers."""

import argparse
import fcntl
import json
import os
import plistlib
import random
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MINUTES_PER_DAY = 1440
WINDOWS = tuple((start, start + 120) for start in range(0, 1440, 120))
DAILY_SCANS = 3
TIMEZONE = ZoneInfo("America/Detroit")
LABEL = "com.wenqi.jobfeed.schedule"
DRAW = random.SystemRandom().randrange
CHOOSE = random.SystemRandom().sample


def plan_path(root, day):
    """Locate the saved choices and attempted slots for one local calendar day."""
    return root / "data/scheduler/random-plans" / f"{day.isoformat()}.json"


def _save(path, plan):
    """Replace a complete plan atomically without changing existing selections."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, indent=2) + "\n")
    temporary.replace(path)


def _read(path, day):
    """Reject malformed plans rather than launching outside the chosen slots."""
    plan = json.loads(path.read_text())
    minutes = plan.get("minutes") if isinstance(plan, dict) else None
    attempted = plan.get("attempted") if isinstance(plan, dict) else None
    if not isinstance(minutes, list) or len(minutes) != DAILY_SCANS:
        raise ValueError("Invalid daily slot count")
    if plan.get("day") != day.isoformat() or not isinstance(attempted, list):
        raise ValueError("Invalid dated scan plan")
    if not all(
        type(value) is int and 0 <= value < MINUTES_PER_DAY for value in minutes
    ):
        raise ValueError("Scan slot outside its window")
    if len({value // 120 for value in minutes}) != DAILY_SCANS:
        raise ValueError("Scan slots must use distinct two-hour windows")
    if any(type(value) is not int or value not in minutes for value in attempted):
        raise ValueError("Invalid attempted scan slot")
    return plan


def ensure_plan(root, day, *, draw=DRAW, choose=CHOOSE):
    """Choose once per day, reusing the choices after restarts or repeated planning."""
    path = plan_path(root, day)
    if path.exists():
        return _read(path, day)
    plan = {
        "day": day.isoformat(),
        "minutes": sorted(
            draw(start, end) for start, end in choose(WINDOWS, DAILY_SCANS)
        ),
        "attempted": [],
    }
    _save(path, plan)
    return _read(path, day)


def run_due(root, day, *, now=None, run=None):
    """Attempt each exact-minute slot once; skip stale or late launchd delivery."""
    now = now or datetime.now(TIMEZONE)
    path = plan_path(root, day)
    if day != now.date() or not path.exists():
        return 0
    lock_path = root / "data/scheduler/random-slot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        plan = _read(path, day)
        minute = now.hour * 60 + now.minute
        if minute not in plan["minutes"] or minute in plan["attempted"]:
            return 0
        # Persist intent before API work; failure or a second DST delivery must
        # not replay a scan and its evaluation.
        plan["attempted"].append(minute)
        _save(path, plan)
        if run is not None:
            return run()
        return subprocess.run(
            [sys.executable, str(root / "scripts/jobfeed_scheduled_cycle.py")],
            check=False,
        ).returncode


def scan_agent(root, plan):
    """Build the dated launch agent whose three calendar triggers were drawn."""
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/jobfeed_random_schedule.py"),
            "run",
            "--day",
            plan["day"],
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": False,
        "StartCalendarInterval": [
            {"Hour": minute // 60, "Minute": minute % 60} for minute in plan["minutes"]
        ],
        "StandardOutPath": str(root / "data/scheduler/random-schedule.log"),
        "StandardErrorPath": str(root / "data/scheduler/random-schedule.log"),
    }


def install_plan(root, plan):
    """Reload only the scan timer; installation never launches a scan itself."""
    service = f"gui/{os.getuid()}/{LABEL}"
    path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    agent = scan_agent(root, plan)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(agent))
    subprocess.run(["plutil", "-lint", str(temporary)], check=True)
    loaded = subprocess.run(
        ["launchctl", "print", service], capture_output=True, check=False
    )
    if loaded.returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)
    temporary.replace(path)
    subprocess.run(["launchctl", "enable", service], check=True)
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True
    )


def main():
    """Plan/install daily random times, or accept an exact dated timer delivery."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument(
        "--day", type=date.fromisoformat, default=datetime.now(TIMEZONE).date()
    )
    parser.add_argument(
        "--run-due",
        action="store_true",
        help="After daily planning, accept a current-minute trigger",
    )
    args = parser.parse_args()
    if args.action == "run":
        return run_due(ROOT, args.day)
    directory = ROOT / "data/scheduler"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "random-plan.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = ensure_plan(ROOT, args.day)
        install_plan(ROOT, plan)
        print(json.dumps(plan))
    return run_due(ROOT, args.day) if args.run_due else 0


if __name__ == "__main__":
    raise SystemExit(main())
