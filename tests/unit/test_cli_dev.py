"""CLI contract for the combined hot-reload development session."""

from __future__ import annotations

import importlib
import signal
import subprocess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from jobfeed.cli import cli

dev_module = importlib.import_module("jobfeed.cli.dev")


def test_dev_command_runs_api_and_vite_under_one_supervisor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The original jobfeed command family owns the hot-reload entrypoint."""
    calls: list[tuple[Path | None, int, int]] = []
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        dev_module,
        "run_dev",
        lambda config_path, api_port, web_port, **_kwargs: (
            calls.append((config_path, api_port, web_port)) or 0
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["dev", "--api-port", "17654", "--web-port", "15173"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(Path("config.toml"), 17654, 15173)]


def test_dev_command_propagates_child_failure(monkeypatch) -> None:
    """A failed API or Vite child makes the foreground command fail."""
    monkeypatch.setattr(dev_module, "run_dev", lambda *_args, **_kwargs: 7)

    result = CliRunner().invoke(cli, ["dev"])

    assert result.exit_code != 0
    assert "development session exited with status 7" in result.output


def test_dev_supervisor_passes_api_port_to_vite_proxy(monkeypatch) -> None:
    """A custom API port is also used by the Vite development proxy."""
    calls: list[dict[str, object]] = []

    class FinishedProcess:
        pid = 12345

        def poll(self) -> int:
            return 0

    def fake_popen(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return FinishedProcess()

    monkeypatch.setattr(dev_module, "_command", lambda _name: "/opt/pnpm")
    monkeypatch.setattr(dev_module, "_release_dev_ports", lambda *_args: None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert dev_module.run_dev(Path("config.toml"), 17654, 15173) == 0
    assert "--reload" not in calls[0]["args"][0]
    vite_environment = calls[1]["kwargs"]["env"]
    assert vite_environment["JOBFEED_DEV_API_PORT"] == "17654"

    calls.clear()
    assert dev_module.run_dev(Path("config.toml"), 17654, 15173, reload_api=True) == 0
    assert "--reload" in calls[0]["args"][0]


def test_dev_stops_stale_jobfeed_process_group_before_starting(monkeypatch) -> None:
    """A prior Jobfeed dev process cannot block the next development session."""
    calls: list[tuple[int, signal.Signals]] = []
    repo = Path("/work/jobfeed")
    stale_pid = 120

    monkeypatch.setattr(dev_module, "_listener_pids", lambda _port: {stale_pid, 121})
    monkeypatch.setattr(dev_module, "_process_group", lambda _pid: stale_pid)
    monkeypatch.setattr(
        dev_module,
        "_process_command",
        lambda pid: (
            "/work/jobfeed/.venv/bin/python -m uvicorn "
            "jobfeed.web.dev_app:create_dev_app --reload-dir /work/jobfeed/src"
            if pid == stale_pid
            else "python -c multiprocessing.spawn"
        ),
    )
    monkeypatch.setattr(
        dev_module.os,
        "killpg",
        lambda pgid, sig: calls.append((pgid, sig)),
    )
    monkeypatch.setattr(dev_module, "_wait_for_ports", lambda _ports: True)

    dev_module._release_dev_ports(repo, [7654])

    assert calls == [(stale_pid, signal.SIGTERM)]


def test_dev_refuses_to_kill_unrelated_port_owner(monkeypatch) -> None:
    """Port cleanup must not terminate an unrelated local application."""
    monkeypatch.setattr(dev_module, "_listener_pids", lambda _port: {987})
    monkeypatch.setattr(dev_module, "_process_group", lambda _pid: 987)
    monkeypatch.setattr(
        dev_module,
        "_process_command",
        lambda _pid: "/usr/local/bin/postgres --port 7654",
    )

    with pytest.raises(click.ClickException, match=r"unrelated process.*987"):
        dev_module._release_dev_ports(Path("/work/jobfeed"), [7654])


@pytest.mark.parametrize(
    "python_path,owned",
    [
        ("/work/jobfeed/.venv/bin/python", True),
        ("/other/jobfeed/.venv/bin/python", False),
        ("/work/jobfeed/.venv/bin/python-unrelated", False),
    ],
)
def test_stable_api_ownership_uses_exact_project_interpreter(
    monkeypatch, python_path, owned
):
    calls = []
    monkeypatch.setattr(dev_module, "_listener_pids", lambda _port: {120})
    monkeypatch.setattr(dev_module, "_process_group", lambda _pid: 120)
    monkeypatch.setattr(
        dev_module,
        "_process_command",
        lambda _pid: (
            f"{python_path} -m uvicorn jobfeed.web.dev_app:create_dev_app "
            "--factory --port 7654"
        ),
    )
    monkeypatch.setattr(
        dev_module.os, "killpg", lambda pgid, sig: calls.append((pgid, sig))
    )
    monkeypatch.setattr(dev_module, "_wait_for_ports", lambda _ports: True)
    if owned:
        dev_module._release_dev_ports(Path("/work/jobfeed"), [7654])
        assert calls == [(120, signal.SIGTERM)]
    else:
        with pytest.raises(click.ClickException, match="unrelated process"):
            dev_module._release_dev_ports(Path("/work/jobfeed"), [7654])
        assert calls == []
