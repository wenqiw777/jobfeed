"""Host-native runtime boundary contracts."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WRAPPER = _ROOT / "bin" / "jobfeed"
_POWERSHELL_WRAPPER = _ROOT / "bin" / "jobfeed.ps1"
_SETUP = _ROOT / "setup"
_SETUP_SH = _ROOT / "setup.sh"
_SCAN = _ROOT / "scan"


def test_normal_wrapper_executes_repo_host_runtime() -> None:
    """Ordinary CLI uses the repo virtual environment directly."""
    wrapper = _WRAPPER.read_text("utf-8")

    assert 'HOST_JOBFEED="$REPO_ROOT/.venv/bin/jobfeed"' in wrapper
    assert 'exec "$HOST_JOBFEED" "$@"' in wrapper
    assert "docker" not in wrapper.lower()


def test_powershell_wrapper_executes_repo_host_runtime() -> None:
    """Windows ordinary CLI uses the repo virtual environment directly."""
    wrapper = _POWERSHELL_WRAPPER.read_text("utf-8")

    assert ".venv\\Scripts\\jobfeed.exe" in wrapper
    assert "docker" not in wrapper.lower()


def test_setup_and_scan_use_host_sqlite() -> None:
    """Setup and scanning have no container or PostgreSQL dependency."""
    setup = _SETUP.read_text("utf-8")
    setup_sh = _SETUP_SH.read_text("utf-8")
    scan = _SCAN.read_text("utf-8")

    assert 'exec "$REPO_ROOT/setup.sh" "$@"' in setup
    assert "sync --locked --no-dev --python 3.12" in setup_sh
    assert "--extra dev" not in setup_sh
    assert "docker" not in setup_sh.lower()
    assert "postgres" not in scan.lower()
    assert "docker" not in scan.lower()


def test_host_sqlite_runtime_state_is_gitignored() -> None:
    """The repo-local production SQLite file and sidecars cannot be committed."""
    ignore = (_ROOT / ".gitignore").read_text("utf-8")

    assert "/data/" in ignore
