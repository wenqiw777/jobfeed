"""Host runtime and migration-only Docker boundary contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_WRAPPER = _ROOT / "bin" / "jobfeed"
_POWERSHELL_WRAPPER = _ROOT / "bin" / "jobfeed.ps1"
_SETUP = _ROOT / "setup"
_SETUP_SH = _ROOT / "setup.sh"
_SCAN = _ROOT / "scan"


def _runtime_service() -> tuple[dict[str, object], dict[str, object]]:
    document = yaml.safe_load(_COMPOSE.read_text("utf-8"))
    return document["services"]["jobfeed-cli"], document


def test_runtime_uses_one_named_sqlite_volume_without_postgres_dependency() -> None:
    """Optional container checks stay isolated from the host default runtime."""
    service, document = _runtime_service()

    assert service["profiles"] == ["container-runtime"]
    assert service["environment"]["JOBFEED_DB_PATH"] == "/data/jobfeed.sqlite"
    assert "JOBFEED_DB_URL" not in service["environment"]
    assert service.get("depends_on") is None
    assert "jobfeed_data:/data" in service["volumes"]
    assert "jobfeed_data" in document["volumes"]
    assert document["services"]["postgres"]["profiles"] == ["migration"]


def test_runtime_uses_host_network_for_loopback_only_web_server() -> None:
    """Container loopback maps to host loopback for the canonical serve path."""
    service, _document = _runtime_service()

    assert service["network_mode"] == "host"


def test_normal_wrapper_executes_repo_host_runtime() -> None:
    """Ordinary CLI uses the repo venv and never enters Docker Compose."""
    wrapper = _WRAPPER.read_text("utf-8")
    marker = "# Host-native daily runtime."

    assert marker in wrapper
    normal_runtime = wrapper.split(marker, 1)[1]
    assert 'HOST_JOBFEED="$REPO_ROOT/.venv/bin/jobfeed"' in normal_runtime
    assert 'exec "$HOST_JOBFEED" "$@"' in normal_runtime
    assert "docker compose" not in normal_runtime


def test_powershell_wrapper_executes_repo_host_runtime() -> None:
    """Windows ordinary CLI uses the repo venv instead of Docker Compose."""
    wrapper = _POWERSHELL_WRAPPER.read_text("utf-8")

    assert ".venv\\Scripts\\jobfeed.exe" in wrapper
    assert "docker compose" not in wrapper


def test_setup_and_scan_do_not_require_postgres_or_docker() -> None:
    """Daily host setup and scanning have no Docker/PostgreSQL dependency."""
    setup = _SETUP.read_text("utf-8")
    setup_sh = _SETUP_SH.read_text("utf-8")
    scan = _SCAN.read_text("utf-8")

    assert 'exec "$REPO_ROOT/setup.sh" "$@"' in setup
    assert "sync --locked --no-dev --python 3.12" in setup_sh
    assert "--extra dev" not in setup_sh
    assert "docker compose" not in setup_sh
    assert "Postgres" not in scan
    assert "docker compose" not in scan


def test_host_sqlite_runtime_state_is_gitignored() -> None:
    """The repo-local production SQLite file and sidecars cannot be committed."""
    ignore = (_ROOT / ".gitignore").read_text("utf-8")

    assert "/data/" in ignore


def test_runtime_image_prepares_writable_data_directory() -> None:
    """The image owns the mounted directory before SQLite opens its file."""
    dockerfile = (_ROOT / "Dockerfile").read_text("utf-8")

    assert "mkdir -p /data" in dockerfile
    assert "JOBFEED_DB_PATH=/data/jobfeed.sqlite" in dockerfile


def test_named_volume_survives_container_removal_and_is_shared() -> None:
    """Two removed containers observe one SQLite file on the named volume."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    probe = subprocess.run(
        ["docker", "info", "--format", "{{json .ServerVersion}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("Docker daemon is unavailable")
    project = f"jobfeed-sqlite-smoke-{os.getpid()}"
    env = {**os.environ, "COMPOSE_PROJECT_NAME": project}
    compose = [
        "docker",
        "compose",
        "--file",
        str(_COMPOSE),
        "--project-directory",
        str(_ROOT),
        "--project-name",
        project,
    ]
    try:
        first = subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "jobfeed-cli",
                "-c",
                "import os,sqlite3; p=os.environ['JOBFEED_DB_PATH']; "
                "c=sqlite3.connect(p); c.execute('create table proof(v text)'); "
                "c.execute(\"insert into proof values('雪')\"); c.commit()",
            ],
            cwd=_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert first.returncode == 0, first.stderr
        second = subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "jobfeed-cli",
                "-c",
                "import json,os,sqlite3; p=os.environ['JOBFEED_DB_PATH']; "
                "print(json.dumps(sqlite3.connect(p).execute("
                "'select v from proof').fetchone()))",
            ],
            cwd=_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout.splitlines()[-1]) == ["雪"]
    finally:
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            cwd=_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
