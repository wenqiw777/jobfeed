"""Unit tests for configuration loading and observability setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfeed.config import (
    LLMSettings,
    ScoringSettings,
    SourcesATSConfig,
    SourcesConfig,
    load_settings,
)
from jobfeed.observability import bind_run_id, configure_logging, get_logger

DEFAULT_STAGE_A_THRESHOLD = 60
ENV_MAX_CONCURRENT = 7
REPO_ROOT = Path(__file__).resolve().parents[2]

# LLM config default values (mirrors LLMSettings defaults)
LLM_DEFAULT_CODEX_TIMEOUT_S = 60.0
LLM_DEFAULT_CLAUDE_TIMEOUT_S = 210.0
LLM_DEFAULT_MAX_CONCURRENT = 4
LLM_DEFAULT_MAX_DAILY_SCORE_CALLS = 150
LLM_DEFAULT_MAX_DAILY_COST_USD = 10.0

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


# --- LLMSettings Phase 3 tests ---


def test_llm_settings_defaults() -> None:
    """LLMSettings defaults should match Phase 3 plan values."""
    cfg = LLMSettings()

    assert cfg.stage_a == "mock/stage-a"
    assert cfg.stage_b == "mock/stage-b"
    assert cfg.codex_timeout_s == LLM_DEFAULT_CODEX_TIMEOUT_S
    assert cfg.claude_timeout_s == LLM_DEFAULT_CLAUDE_TIMEOUT_S
    assert cfg.max_concurrent == LLM_DEFAULT_MAX_CONCURRENT
    assert cfg.master_resume_path == ".jobfeed-dev/resume.md"
    assert cfg.preamble_personal_path is None
    assert cfg.max_daily_score_calls == LLM_DEFAULT_MAX_DAILY_SCORE_CALLS
    assert cfg.max_daily_cost_usd == LLM_DEFAULT_MAX_DAILY_COST_USD


def test_llm_settings_validates_provider_format() -> None:
    """stage_a and stage_b must contain a '/' separator."""
    with pytest.raises(ValidationError, match="backend/model"):
        LLMSettings(stage_a="no-slash")


def test_llm_settings_accepts_all_backends() -> None:
    """All three backend prefixes should pass validation."""
    for spec in [
        "codex-cli/gpt-5.4-mini",
        "claude-cli/claude-haiku-4-5",
        "mock/stage-a",
    ]:
        cfg = LLMSettings(stage_a=spec)
        assert cfg.stage_a == spec


def test_llm_settings_rejects_extra_fields() -> None:
    """extra='forbid' should reject unknown fields."""
    with pytest.raises(ValidationError):
        LLMSettings(nonexistent_field="value")  # type: ignore[call-arg]


LLM_TOML_CODEX_TIMEOUT = 90.0
LLM_TOML_CLAUDE_TIMEOUT = 300.0
LLM_TOML_DAILY_CALLS = 200
LLM_TOML_DAILY_COST = 20.0


def test_load_settings_parses_llm_section(tmp_path: Path) -> None:
    """load_settings should populate llm fields from [llm] TOML section."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[llm]\n"
        'stage_a = "codex-cli/gpt-5.4-mini"\n'
        'stage_b = "codex-cli/gpt-5.5"\n'
        f"codex_timeout_s = {LLM_TOML_CODEX_TIMEOUT:.0f}\n"
        f"claude_timeout_s = {LLM_TOML_CLAUDE_TIMEOUT:.0f}\n"
        f"max_daily_score_calls = {LLM_TOML_DAILY_CALLS}\n"
        f"max_daily_cost_usd = {LLM_TOML_DAILY_COST}\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.llm.stage_a == "codex-cli/gpt-5.4-mini"
    assert settings.llm.stage_b == "codex-cli/gpt-5.5"
    assert settings.llm.codex_timeout_s == LLM_TOML_CODEX_TIMEOUT
    assert settings.llm.claude_timeout_s == LLM_TOML_CLAUDE_TIMEOUT
    assert settings.llm.max_daily_score_calls == LLM_TOML_DAILY_CALLS
    assert settings.llm.max_daily_cost_usd == LLM_TOML_DAILY_COST


def test_scoring_settings_rejects_max_daily_score_calls() -> None:
    """max_daily_score_calls moved to LLMSettings; ScoringSettings rejects it."""
    with pytest.raises(ValidationError):
        ScoringSettings(max_daily_score_calls=100)  # type: ignore[call-arg]


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
