"""CLI contract for the combined hot-reload development session."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

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
        lambda config_path, api_port, web_port: (
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
    monkeypatch.setattr(dev_module, "run_dev", lambda *_args: 7)

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
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert dev_module.run_dev(Path("config.toml"), 17654, 15173) == 0
    vite_environment = calls[1]["kwargs"]["env"]
    assert vite_environment["JOBFEED_DEV_API_PORT"] == "17654"
