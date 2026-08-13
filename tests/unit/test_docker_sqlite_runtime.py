"""Docker runtime contracts for the persistent SQLite cutover boundary."""

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


def _runtime_service() -> tuple[dict[str, object], dict[str, object]]:
    document = yaml.safe_load(_COMPOSE.read_text("utf-8"))
    return document["services"]["jobfeed-cli"], document


def test_runtime_uses_one_named_sqlite_volume_without_postgres_dependency() -> None:
    """Every normal container receives the same path on one named volume."""
    service, document = _runtime_service()

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


def test_normal_wrapper_names_reviewed_compose_file_and_runtime_service() -> None:
    """Ordinary CLI cannot be redirected through COMPOSE_FILE or PostgreSQL."""
    wrapper = _WRAPPER.read_text("utf-8")

    assert 'docker compose --file "$REPO_ROOT/docker-compose.yml"' in wrapper
    assert "unset COMPOSE_FILE COMPOSE_PATH_SEPARATOR" in wrapper
    assert 'jobfeed-cli jobfeed "$@"' in wrapper


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
