"""Acceptance tests for the clone-to-GUI setup entrypoint."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPEATED_OPEN_COUNT = 2


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fresh_checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    checkout = tmp_path / "jobfeed"
    (checkout / "bin").mkdir(parents=True)
    (checkout / "web-ui" / "dist" / "assets").mkdir(parents=True)
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / "fake-bin").mkdir()
    shutil.copy2(REPO_ROOT / "setup.sh", checkout / "setup.sh")
    shutil.copy2(REPO_ROOT / "setup", checkout / "setup")
    shutil.copy2(REPO_ROOT / "config.example.toml", checkout / "config.example.toml")
    (checkout / "web-ui" / "dist" / "index.html").write_text(
        "<html>jobfeed</html>", encoding="utf-8"
    )
    (checkout / "web-ui" / "dist" / "assets" / "app.js").write_text(
        "", encoding="utf-8"
    )
    (checkout / ".venv" / "bin" / "python").symlink_to(sys.executable)

    _write_executable(
        checkout / "fake-bin" / "uv",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$SETUP_TEST_UV_LOG"\n',
    )
    _write_executable(
        checkout / "fake-bin" / "curl",
        '#!/bin/sh\n[ -f "$SETUP_TEST_ROOT/data/jobfeed-serve.pid" ]\n',
    )
    _write_executable(
        checkout / "fake-bin" / "open",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$SETUP_TEST_OPEN_LOG"\n',
    )
    _write_executable(
        checkout / "bin" / "jobfeed",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$SETUP_TEST_SERVE_LOG"\n'
        '[ "${1:-}" = serve ] && sleep 30\n',
    )

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{checkout / 'fake-bin'}:/usr/bin:/bin",
            "SETUP_TEST_ROOT": str(checkout),
            "SETUP_TEST_UV_LOG": str(checkout / "uv.log"),
            "SETUP_TEST_OPEN_LOG": str(checkout / "open.log"),
            "SETUP_TEST_SERVE_LOG": str(checkout / "serve.log"),
        }
    )
    return checkout, environment


def _stop_test_server(checkout: Path) -> None:
    pid_path = checkout / "data" / "jobfeed-serve.pid"
    if not pid_path.exists():
        return
    with suppress(ProcessLookupError):
        os.kill(int(pid_path.read_text(encoding="utf-8")), 15)


def test_setup_sh_prepares_runtime_starts_server_and_opens_setup(
    tmp_path: Path,
) -> None:
    """A fresh checkout reaches GUI configuration through one setup command."""
    checkout, environment = _fresh_checkout(tmp_path)
    try:
        result = subprocess.run(
            ["./setup.sh"],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert not (checkout / "config.toml").exists()
        assert (checkout / "data" / "jobfeed-serve.pid").is_file()
        assert (checkout / "data" / "jobfeed-serve.log").is_file()
        assert (checkout / "uv.log").read_text(encoding="utf-8").strip() == (
            "sync --locked --no-dev --python 3.12"
        )
        assert (checkout / "serve.log").read_text(encoding="utf-8").splitlines() == [
            "serve"
        ]
        assert (checkout / "open.log").read_text(encoding="utf-8").strip() == (
            "http://127.0.0.1:7654/setup"
        )
        assert "Jobfeed is ready" in result.stdout
    finally:
        _stop_test_server(checkout)


def test_setup_is_idempotent_when_gui_is_already_healthy(tmp_path: Path) -> None:
    """Repeating setup reopens the GUI without starting a duplicate server."""
    checkout, environment = _fresh_checkout(tmp_path)
    try:
        first = subprocess.run(
            ["./setup.sh"],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert first.returncode == 0, first.stderr
        second = subprocess.run(
            ["./setup.sh"],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert second.returncode == 0, second.stderr
        assert (checkout / "serve.log").read_text(encoding="utf-8").splitlines() == [
            "serve"
        ]
        assert (
            len((checkout / "open.log").read_text(encoding="utf-8").splitlines())
            == REPEATED_OPEN_COUNT
        )
        assert set((checkout / "open.log").read_text().splitlines()) == {
            "http://127.0.0.1:7654/setup"
        }
    finally:
        _stop_test_server(checkout)


def test_setup_reopens_main_gui_after_configuration_exists(tmp_path: Path) -> None:
    """A configured checkout bypasses onboarding on later setup runs."""
    checkout, environment = _fresh_checkout(tmp_path)
    (checkout / "config.toml").write_text("[db]\npath = 'data/jobfeed.sqlite'\n")
    try:
        result = subprocess.run(
            ["./setup.sh"],
            cwd=checkout,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert (checkout / "open.log").read_text().strip() == ("http://127.0.0.1:7654")
    finally:
        _stop_test_server(checkout)


def test_fresh_clone_contains_the_prebuilt_gui() -> None:
    """The one-command path never requires Node on an end-user machine."""
    index = REPO_ROOT / "web-ui" / "dist" / "index.html"
    assert index.is_file()
    assert (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(index.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )
    asset_paths = re.findall(r'["\'](/assets/[^"\']+)["\']', index.read_text())
    assert asset_paths
    assert all(
        (REPO_ROOT / "web-ui" / "dist" / path.lstrip("/")).is_file()
        for path in asset_paths
    )


def test_legacy_setup_name_delegates_to_setup_sh() -> None:
    """Existing documentation and user muscle memory remain compatible."""
    body = (REPO_ROOT / "setup").read_text(encoding="utf-8")
    assert 'exec "$REPO_ROOT/setup.sh" "$@"' in body
