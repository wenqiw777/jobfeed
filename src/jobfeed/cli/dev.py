"""Combined stable API and hot-reloading Vite supervisor."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import click

from jobfeed.cli import require_app

_DEFAULT_API_PORT = 7654
_DEFAULT_WEB_PORT = 5173
_STOP_GRACE_SECONDS = 5.0
_PORT_RELEASE_SECONDS = 3.0


@click.command(name="dev", help="Run a stable API and hot-reloading web UI.")
@click.option("--api-port", default=_DEFAULT_API_PORT, show_default=True, type=int)
@click.option("--web-port", default=_DEFAULT_WEB_PORT, show_default=True, type=int)
@click.option(
    "--reload-api",
    is_flag=True,
    help="Reload API on source edits; interrupts active scans.",
)
@click.pass_context
def dev(ctx: click.Context, api_port: int, web_port: int, reload_api: bool) -> None:
    """Run both development servers until one exits or Ctrl-C is pressed.

    Args:
        ctx: Click context containing the selected Jobfeed configuration.
        api_port: Loopback port for the reloading API.
        web_port: Loopback port for the Vite development UI.
        reload_api: Opt in to API reloads that interrupt active work.

    Raises:
        click.ClickException: If a child exits unsuccessfully or pnpm is absent.
    """
    app = require_app(ctx)
    code = run_dev(app["config_path"], api_port, web_port, reload_api=reload_api)
    if code != 0:
        raise click.ClickException(f"development session exited with status {code}")


def run_dev(
    config_path: Path | None, api_port: int, web_port: int, *, reload_api: bool = False
) -> int:
    """Supervise the stable API and Vite as one foreground process.

    Args:
        config_path: Configuration selected by the parent CLI.
        api_port: Loopback port for the reloading API.
        web_port: Loopback port for the Vite development UI.
        reload_api: Opt in to API reloads that interrupt active work.

    Returns:
        Zero after Ctrl-C, or the first child process failure status.
    """
    repo = Path(__file__).resolve().parents[3]
    pnpm = _command("pnpm")
    _release_dev_ports(repo, [api_port, web_port])
    environment = os.environ.copy()
    environment["JOBFEED_DEV_API_PORT"] = str(api_port)
    if config_path is not None:
        environment["JOBFEED_DEV_CONFIG"] = str(config_path)
    processes: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "jobfeed.web.dev_app:create_dev_app",
                    "--factory",
                    *(
                        ["--reload", "--reload-dir", str(repo / "src")]
                        if reload_api
                        else []
                    ),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                    "--no-access-log",
                ],
                cwd=repo,
                env=environment,
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
                    str(web_port),
                ],
                cwd=repo,
                env=environment,
                start_new_session=True,
            )
        )
        click.echo(
            f"Jobfeed API ({'reload enabled' if reload_api else 'stable'}): "
            f"http://127.0.0.1:{api_port} · "
            f"UI http://localhost:{web_port} · Ctrl-C stops both"
        )
        while not stop_requested:
            code = _finished_code(processes)
            if code is not None:
                return code
            time.sleep(0.1)
        return 0
    finally:
        _stop(processes)
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def _command(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise click.ClickException(f"required development command not found: {name}")
    return resolved


def _release_dev_ports(repo: Path, ports: list[int]) -> None:
    """Stop stale Jobfeed development process groups holding requested ports."""
    listeners: dict[int, set[int]] = {port: _listener_pids(port) for port in ports}
    listeners = {port: pids for port, pids in listeners.items() if pids}
    if not listeners:
        return

    groups: dict[int, set[int]] = {}
    for pid in set().union(*listeners.values()):
        groups.setdefault(_process_group(pid), set()).add(pid)

    repo_text = str(repo)
    stale_groups: set[int] = set()
    for group, pids in groups.items():
        commands = {pid: _process_command(pid) for pid in pids}
        owned = any(
            (
                "jobfeed.web.dev_app:create_dev_app" in command
                and (
                    str(repo / "src") in command
                    or command.startswith(
                        f"{repo / '.venv/bin/python'} -m uvicorn "
                        "jobfeed.web.dev_app:create_dev_app --factory "
                    )
                )
            )
            or ("vite" in command and str(repo / "web-ui") in command)
            or ("jobfeed dev" in command and repo_text in command)
            for command in commands.values()
        )
        if not owned:
            details = ", ".join(
                f"PID {pid} ({command or 'unknown command'})"
                for pid, command in sorted(commands.items())
            )
            occupied = ", ".join(str(port) for port in sorted(listeners))
            raise click.ClickException(
                f"development port {occupied} is held by unrelated process: {details}"
            )
        stale_groups.add(group)

    for group in stale_groups:
        click.echo(f"Stopping stale Jobfeed development process group {group}...")
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGTERM)

    if _wait_for_ports(list(listeners)):
        return

    for group in stale_groups:
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
    if not _wait_for_ports(list(listeners)):
        occupied = ", ".join(str(port) for port in sorted(listeners))
        raise click.ClickException(f"could not release development port(s): {occupied}")


def _listener_pids(port: int) -> set[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        check=False,
        text=True,
    )
    return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}


def _process_group(pid: int) -> int:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "pgid="],
        capture_output=True,
        check=False,
        text=True,
    )
    value = result.stdout.strip()
    return int(value) if value.isdigit() else pid


def _process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip()


def _wait_for_ports(ports: list[int]) -> bool:
    deadline = time.monotonic() + _PORT_RELEASE_SECONDS
    while time.monotonic() < deadline:
        if not any(_listener_pids(port) for port in ports):
            return True
        time.sleep(0.05)
    return not any(_listener_pids(port) for port in ports)


def _finished_code(processes: list[subprocess.Popen[bytes]]) -> int | None:
    for process in processes:
        code = process.poll()
        if code is not None:
            return code
    return None


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    running = [process for process in processes if process.poll() is None]
    for process in running:
        _signal_group(process, signal.SIGINT)
    deadline = time.monotonic() + _STOP_GRACE_SECONDS
    while running and time.monotonic() < deadline:
        running = [process for process in running if process.poll() is None]
        time.sleep(0.05)
    for process in running:
        _signal_group(process, signal.SIGTERM)
    for process in running:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait()


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


__all__ = ["dev", "run_dev"]
