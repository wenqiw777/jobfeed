"""Unit tests for configuration loading and observability setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobfeed.config import load_settings
from jobfeed.observability import bind_run_id, configure_logging, get_logger

DEFAULT_STAGE_A_THRESHOLD = 60
ENV_MAX_CONCURRENT = 7
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_load_settings_returns_defaults_without_config_file() -> None:
    """Omitting config should fall back to repo-local Phase 0 defaults."""
    settings = load_settings()

    assert settings.db.backend == "sqlite"
    assert settings.db.sqlite_path == Path(".jobfeed-dev/dev.db")
    assert "~/.jobfeed" not in str(settings.db.sqlite_path)
    assert settings.llm.stage_a == "mock/stage-a"


def test_load_settings_rejects_missing_explicit_config(tmp_path: Path) -> None:
    """An explicit missing config path should fail instead of silently defaulting.

    Args:
        tmp_path: Temporary directory used to point at a missing config file.
    """
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_settings(tmp_path / "missing.toml")


def test_load_settings_accepts_config_example() -> None:
    """The checked-in example config should validate successfully."""
    settings = load_settings(REPO_ROOT / "config.example.toml")

    assert settings.db.backend == "sqlite"
    assert settings.scoring.stage_a_threshold == DEFAULT_STAGE_A_THRESHOLD
    assert settings.observability.log_format == "human"


def test_load_settings_env_overrides_file_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JOBFEED env vars should override TOML values explicitly.

    Args:
        tmp_path: Temporary directory for a synthetic config file.
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text('[db]\nbackend = "sqlite"\n', encoding="utf-8")
    monkeypatch.setenv("JOBFEED_DB__BACKEND", "postgres")

    settings = load_settings(config_path)

    assert settings.db.backend == "postgres"


def test_load_settings_env_overrides_nested_numeric_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic validation should coerce explicit env string overrides.

    Args:
        tmp_path: Temporary directory for a synthetic config file.
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text("[llm]\nmax_concurrent = 2\n", encoding="utf-8")
    monkeypatch.setenv("JOBFEED_LLM__MAX_CONCURRENT", str(ENV_MAX_CONCURRENT))

    settings = load_settings(config_path)

    assert settings.llm.max_concurrent == ENV_MAX_CONCURRENT


def test_configure_logging_json_includes_bound_run_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON logging should emit machine-readable events with context."""
    configure_logging("info", "json")
    bind_run_id("test-123")

    get_logger().info("json-check", component="test")

    output = capsys.readouterr().out.strip()
    event = json.loads(output)
    assert event["event"] == "json-check"
    assert event["component"] == "test"
    assert event["run_id"] == "test-123"
    assert event["level"] == "info"


def test_configure_logging_human_outputs_readable_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human logging should produce readable console output."""
    configure_logging("info", "human")

    get_logger().info("human-check", component="test")

    output = capsys.readouterr().out
    assert "human-check" in output
    assert "component" in output
    assert "\x1b[" not in output
