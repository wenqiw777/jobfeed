"""Run the Jobfeed API and Vite in one interruptible foreground session."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

_STOP_GRACE_SECONDS = 5.0


def _command(name: str, preferred: Path | None = None) -> str:
    if preferred is not None and preferred.is_file():
        return str(preferred)
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeError(f"required command not found: {name}")
    return resolved


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    running = [process for process in processes if process.poll() is None]
    for process in running:
        os.killpg(process.pid, signal.SIGINT)
    deadline = time.monotonic() + _STOP_GRACE_SECONDS
    while running and time.monotonic() < deadline:
        running = [process for process in running if process.poll() is None]
        time.sleep(0.05)
    for process in running:
        os.killpg(process.pid, signal.SIGTERM)
    for process in running:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    jobfeed = _command("jobfeed", repo / ".venv" / "bin" / "jobfeed")
    pnpm = _command("pnpm")
    api_port = os.environ.get("JOBFEED_DEV_API_PORT", "7654")
    web_port = os.environ.get("JOBFEED_DEV_WEB_PORT", "5173")
    processes: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        processes.append(
            subprocess.Popen(
                [jobfeed, "serve", "--port", api_port],
                cwd=repo,
                start_new_session=True,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    pnpm,
                    "--dir",
                    str(repo / "web-ui"),
                    "dev",
                    "--port",
                    web_port,
                ],
                cwd=repo,
                start_new_session=True,
            )
        )
        print("Jobfeed API + web UI running. Press Ctrl-C to stop both.", flush=True)
        while not stop_requested:
            for process in processes:
                code = process.poll()
                if code is not None:
                    return code
            time.sleep(0.1)
        return 0
    finally:
        _stop(processes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
