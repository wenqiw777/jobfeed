"""Unit tests for configuration loading and observability setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfeed.config import SourcesATSConfig, SourcesConfig, load_settings
from jobfeed.observability import bind_run_id, configure_logging, get_logger

DEFAULT_STAGE_A_THRESHOLD = 60
ENV_MAX_CONCURRENT = 7
REPO_ROOT = Path(__file__).resolve().parents[2]

# ATS config default values (mirrors SourcesATSConfig defaults)
ATS_DEFAULT_MAX_CONCURRENT = 10
ATS_DEFAULT_PROBE_TTL_DAYS = 7
ATS_DEFAULT_FAILURE_THRESHOLD = 3
ATS_DEFAULT_PROBE_TIMEOUT_S = 5.0
ATS_DEFAULT_SCAN_TIMEOUT_S = 30.0
ATS_ENV_MAX_CONCURRENT = 5


def test_load_settings_returns_defaults_without_config_file() -> None:
    """Omitting config should fall back to repo-local defaults (no DSN set)."""
    settings = load_settings()

    assert settings.db.url is None
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

    assert settings.db.url is not None
    assert settings.db.url.startswith("postgresql://")
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
    config_path.write_text(
        '[db]\nurl = "postgresql://file@host/db"\n', encoding="utf-8"
    )
    monkeypatch.setenv("JOBFEED_DB__URL", "postgresql://env@host/db")

    settings = load_settings(config_path)

    assert settings.db.url == "postgresql://env@host/db"


def test_load_settings_flat_db_url_alias_maps_to_db_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flat ``JOBFEED_DB_URL`` alias must map to ``db.url`` and validate.

    Regression: ``_collect_env_overrides`` splits only on ``__``, so the flat
    var also landed as a top-level ``db_url`` key. ``Settings`` forbids extra
    fields, so config loading failed whenever Docker/CI set ``JOBFEED_DB_URL``.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    url = "postgresql://jobfeed:jobfeed_dev@postgres:5432/jobfeed_dev"
    monkeypatch.setenv("JOBFEED_DB_URL", url)

    settings = load_settings()

    assert settings.db.url == url


def test_load_settings_nested_db_url_beats_flat_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nested ``JOBFEED_DB__URL`` form takes precedence over the flat alias.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    monkeypatch.setenv("JOBFEED_DB_URL", "postgresql://flat@host/db")
    monkeypatch.setenv("JOBFEED_DB__URL", "postgresql://nested@host/db")

    settings = load_settings()

    assert settings.db.url == "postgresql://nested@host/db"


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


# --- SourcesATSConfig / SourcesConfig tests ---


def test_sources_ats_config_defaults() -> None:
    """SourcesATSConfig should produce correct defaults when created bare."""
    cfg = SourcesATSConfig()

    assert cfg.enabled is True
    assert cfg.max_concurrent == ATS_DEFAULT_MAX_CONCURRENT
    assert cfg.probe_ttl_days == ATS_DEFAULT_PROBE_TTL_DAYS
    assert cfg.failure_threshold == ATS_DEFAULT_FAILURE_THRESHOLD
    assert cfg.probe_timeout_s == ATS_DEFAULT_PROBE_TIMEOUT_S
    assert cfg.scan_timeout_s == ATS_DEFAULT_SCAN_TIMEOUT_S
    assert cfg.seed_companies == []


def test_sources_config_wraps_ats_config() -> None:
    """SourcesConfig should nest SourcesATSConfig under the ``ats`` key."""
    cfg = SourcesConfig()

    assert isinstance(cfg.ats, SourcesATSConfig)


def test_settings_has_sources_field() -> None:
    """Root Settings should expose a ``sources`` field of type SourcesConfig."""
    settings = load_settings()

    assert isinstance(settings.sources, SourcesConfig)
    assert isinstance(settings.sources.ats, SourcesATSConfig)


def test_load_settings_parses_ats_section(tmp_path: Path) -> None:
    """load_settings should populate sources.ats from a [sources.ats] TOML section.

    Args:
        tmp_path: Temporary directory for a synthetic config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[sources.ats]\nseed_companies = ["anthropic", "openai"]\n'
        f"max_concurrent = {ATS_ENV_MAX_CONCURRENT}\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.sources.ats.seed_companies == ["anthropic", "openai"]
    assert settings.sources.ats.max_concurrent == ATS_ENV_MAX_CONCURRENT


def test_load_settings_example_config_has_ats_seed_companies() -> None:
    """The checked-in example config should include ATS seed companies."""
    settings = load_settings(REPO_ROOT / "config.example.toml")

    assert "anthropic" in settings.sources.ats.seed_companies
    assert "openai" in settings.sources.ats.seed_companies
    assert "palantir" in settings.sources.ats.seed_companies


def test_load_settings_env_overrides_ats_max_concurrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JOBFEED_SOURCES__ATS__MAX_CONCURRENT env var should override the config value.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    monkeypatch.setenv(
        "JOBFEED_SOURCES__ATS__MAX_CONCURRENT", str(ATS_ENV_MAX_CONCURRENT)
    )

    settings = load_settings()

    assert settings.sources.ats.max_concurrent == ATS_ENV_MAX_CONCURRENT


def test_sources_ats_config_rejects_max_concurrent_zero() -> None:
    """max_concurrent=0 should fail Pydantic validation (ge=1 constraint)."""
    with pytest.raises(ValidationError):
        SourcesATSConfig(max_concurrent=0)


def test_sources_ats_config_rejects_failure_threshold_zero() -> None:
    """failure_threshold=0 should fail Pydantic validation (ge=1 constraint)."""
    with pytest.raises(ValidationError):
        SourcesATSConfig(failure_threshold=0)


def test_sources_ats_config_rejects_negative_probe_ttl_days() -> None:
    """probe_ttl_days < 0 should fail Pydantic validation (ge=0 constraint)."""
    with pytest.raises(ValidationError):
        SourcesATSConfig(probe_ttl_days=-1)


def test_sources_ats_config_rejects_nonpositive_probe_timeout() -> None:
    """probe_timeout_s <= 0 should fail Pydantic validation (gt=0 constraint)."""
    with pytest.raises(ValidationError):
        SourcesATSConfig(probe_timeout_s=0.0)


def test_sources_ats_config_rejects_nonpositive_scan_timeout() -> None:
    """scan_timeout_s <= 0 should fail Pydantic validation (gt=0 constraint)."""
    with pytest.raises(ValidationError):
        SourcesATSConfig(scan_timeout_s=0.0)
