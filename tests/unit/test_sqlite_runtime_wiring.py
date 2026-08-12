"""SQLite-only runtime configuration and dependency wiring contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.cli import cli, create_app
from jobfeed.config import Settings, load_settings
from jobfeed.services.run_orchestration import RunLeaseOrchestrator
from jobfeed.web.app import create_web_app


def test_settings_default_and_file_use_explicit_sqlite_path(tmp_path: Path) -> None:
    """Runtime config exposes one path and resolves relative files predictably."""
    assert load_settings().db.path == Path("data/jobfeed.sqlite")
    config = tmp_path / "config.toml"
    config.write_text('[db]\npath = "shared/jobfeed.sqlite"\n', encoding="utf-8")

    settings = load_settings(config)

    assert settings.db.path == tmp_path / "shared/jobfeed.sqlite"


def test_nested_db_path_beats_flat_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """JOBFEED_DB__PATH wins over the flat JOBFEED_DB_PATH alias."""
    monkeypatch.setenv("JOBFEED_DB_PATH", "flat.sqlite")
    monkeypatch.setenv("JOBFEED_DB__PATH", "nested.sqlite")

    assert load_settings().db.path == Path("nested.sqlite")


@pytest.mark.parametrize(
    ("environment", "config"),
    [
        ({"JOBFEED_DB_URL": "postgresql://legacy/db"}, ""),
        ({"JOBFEED_DB__URL": "postgresql://legacy/db"}, ""),
        ({}, '[db]\nurl = "postgresql://legacy/db"\n'),
    ],
)
def test_runtime_rejects_legacy_postgres_url_with_migration_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    config: str,
) -> None:
    """Normal runtime never silently ignores a legacy PostgreSQL URL."""
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "config.toml"
    path.write_text(config, encoding="utf-8")

    with pytest.raises(ValidationError, match="migrate pg-to-sqlite"):
        load_settings(path)


def test_create_app_wires_native_sqlite_capabilities(tmp_path: Path) -> None:
    """CLI and Web share SQLiteStore, native leases, and native threshold sync."""
    database = tmp_path / "shared.sqlite"
    config = tmp_path / "config.toml"
    config.write_text(f'[db]\npath = "{database}"\n', encoding="utf-8")

    app = create_app(config)

    assert isinstance(app["store"], SQLiteStore)
    assert app["settings"].db.path == database
    assert isinstance(app["run_orchestrator"], RunLeaseOrchestrator)
    assert app["stage_b_threshold_sync"] is app["store"]
    assert app["run_orchestrator"]._store is app["store"]

    web_app = create_web_app(config)

    assert isinstance(web_app.state.context["store"], SQLiteStore)
    assert web_app.state.context["settings"].db.path == database


def test_cli_reports_legacy_url_errors_without_traceback(
    tmp_path: Path,
) -> None:
    """Invalid runtime persistence config is a clean Click error."""
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('[db]\nurl = "postgresql://legacy/db"\n', encoding="utf-8")

    result = CliRunner().invoke(cli, ["--config", str(legacy), "scan"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "migrate pg-to-sqlite" in result.output


def test_settings_model_has_no_backend_or_url_selector() -> None:
    """The public runtime model has one SQLite path, not a backend selector."""
    assert set(Settings.model_fields["db"].annotation.model_fields) == {"path"}


@pytest.mark.parametrize("env_name", ["JOBFEED_DB_PATH", "JOBFEED_DB__PATH"])
def test_empty_sqlite_environment_path_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    """Both supported path forms reject an unusable empty filename."""
    monkeypatch.setenv(env_name, "   ")

    with pytest.raises(ValidationError, match=r"db\.path must not be empty"):
        load_settings()


def test_cli_reports_empty_sqlite_path_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed path environment override stays a clean Click error."""
    monkeypatch.setenv("JOBFEED_DB_PATH", "")

    result = CliRunner().invoke(cli, ["scan"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "db.path must not be empty" in result.output


def test_cli_scan_persists_through_native_sqlite_run_lease(tmp_path: Path) -> None:
    """A real CLI scan writes jobs and finalizes its fenced SQLite run."""
    database = tmp_path / "shared.sqlite"
    config = tmp_path / "config.toml"
    config.write_text(f'[db]\npath = "{database}"\n', encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["--config", str(config), "scan", "--source", "mock"]
    )

    assert result.exit_code == 0, result.output
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM jobs").fetchone() == (3,)
        assert connection.execute("SELECT status FROM pipeline_runs").fetchone() == (
            "succeeded",
        )
        assert connection.execute(
            "SELECT count(*) FROM run_leases WHERE owner_id IS NOT NULL"
        ).fetchone() == (0,)
