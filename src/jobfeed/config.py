"""Configuration loading for the Phase 0 Jobfeed runtime."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIG_ENV_PREFIX = "JOBFEED_"
ENV_NESTED_DELIMITER = "__"


class DBSettings(BaseModel):
    """PostgreSQL connection settings validated after TOML and env merging.

    Postgres is the only supported backend. ``url`` is the asyncpg/libpq DSN;
    when omitted, the CLI falls back to its built-in development DSN.
    """

    model_config = ConfigDict(extra="forbid")

    url: str | None = None


class LLMSettings(BaseModel):
    """LLM model and runtime limits used by evaluation services."""

    model_config = ConfigDict(extra="forbid")

    stage_a: str = "mock/stage-a"
    stage_b: str = "mock/stage-b"
    codex_timeout_s: float = 60.0
    claude_timeout_s: float = 210.0
    max_concurrent: int = 4
    master_resume_path: str = ".jobfeed-dev/resume.md"
    preamble_personal_path: str | None = None
    max_daily_score_calls: int = 150
    max_daily_cost_usd: float = 10.0

    @field_validator("stage_a", "stage_b")
    @classmethod
    def _validate_provider_format(cls, v: str) -> str:
        if "/" not in v:
            msg = f"must be in 'backend/model' format, got {v!r}"
            raise ValueError(msg)
        return v


class ScoringSettings(BaseModel):
    """Scoring gates used by evaluation services."""

    model_config = ConfigDict(extra="forbid")

    stage_a_threshold: int = 60
    ml_gate_enabled: bool = False


class ExecutionSettings(BaseModel):
    """Execution mode settings for local workflow orchestration."""

    model_config = ConfigDict(extra="forbid")

    default_runner: str = "in_process"


class ObservabilitySettings(BaseModel):
    """Logging settings shared by CLI and service entry points."""

    model_config = ConfigDict(extra="forbid")

    log_level: str = "info"
    log_format: Literal["human", "json"] = "human"


class SourcesATSConfig(BaseModel):
    """Runtime limits and tuning knobs for the ATS source."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_concurrent: int = Field(default=10, ge=1)
    probe_ttl_days: int = Field(default=7, ge=0)
    failure_threshold: int = Field(default=3, ge=1)
    probe_timeout_s: float = Field(default=5.0, gt=0)
    scan_timeout_s: float = Field(default=30.0, gt=0)
    seed_companies: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    """Container for all job-data source configurations."""

    model_config = ConfigDict(extra="forbid")

    ats: SourcesATSConfig = Field(default_factory=SourcesATSConfig)


class Settings(BaseModel):
    """Validated top-level application settings."""

    model_config = ConfigDict(extra="forbid")

    db: DBSettings = Field(default_factory=DBSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from optional TOML plus explicit JOBFEED env overrides.

    Args:
        config_path: Optional path to a TOML config file. Omit this argument to
            use local Phase 0 defaults.

    Returns:
        Validated settings with env values taking precedence over file values.

    Raises:
        FileNotFoundError: If an explicit config path does not exist.
    """
    file_data = _load_toml_file(config_path)
    env_data = _collect_env_overrides(os.environ)
    _apply_convenience_env_vars(os.environ, env_data)
    merged = _merge_dicts(file_data, env_data)
    return Settings.model_validate(merged)


def _load_toml_file(config_path: Path | None) -> dict[str, object]:
    if config_path is None:
        return {}
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as config_file:
        return cast(dict[str, object], tomllib.load(config_file))


def _collect_env_overrides(environ: Mapping[str, str]) -> dict[str, object]:
    """Collect explicit nested JOBFEED env overrides.

    Args:
        environ: Environment mapping to inspect.

    Returns:
        Nested dictionary suitable for merging into TOML config.

    Notes:
        Time complexity: O(E * D), where E is the number of environment
        variables and D is the number of nested path segments per override.
    """
    overrides: dict[str, object] = {}
    for key, value in environ.items():
        if not key.startswith(CONFIG_ENV_PREFIX):
            continue
        path = key.removeprefix(CONFIG_ENV_PREFIX).lower().split(ENV_NESTED_DELIMITER)
        if not all(path):
            continue
        _set_nested_value(overrides, path, value)
    return overrides


def _apply_convenience_env_vars(
    environ: Mapping[str, str],
    overrides: dict[str, object],
) -> None:
    """Apply convenience (non-nested) env var aliases into the overrides dict.

    ``JOBFEED_DB_URL`` is a flat env var that maps to ``db.url`` for ergonomic
    use in Docker Compose and shell scripts.  The nested form
    ``JOBFEED_DB__URL`` takes precedence if both are set.
    """
    db_url = environ.get("JOBFEED_DB_URL")
    if db_url is not None:
        # ``_collect_env_overrides`` splits only on ``__``, so ``JOBFEED_DB_URL``
        # lands as a spurious top-level ``db_url`` key. ``Settings`` forbids extra
        # fields, so drop that flat key and remap the value to ``db.url`` instead.
        overrides.pop("db_url", None)
        db_section = overrides.setdefault("db", {})
        if isinstance(db_section, dict):
            db_section.setdefault("url", db_url)


def _set_nested_value(target: dict[str, object], path: list[str], value: str) -> None:
    current = target
    for part in path[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = cast(dict[str, object], next_value)
    current[path[-1]] = value


def _merge_dicts(
    base: dict[str, object],
    overrides: dict[str, object],
) -> dict[str, object]:
    merged = dict(base)
    for key, override_value in overrides.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = _merge_dicts(
                cast(dict[str, object], base_value),
                cast(dict[str, object], override_value),
            )
            continue
        merged[key] = override_value
    return merged


__all__ = [
    "DBSettings",
    "ExecutionSettings",
    "LLMSettings",
    "ObservabilitySettings",
    "ScoringSettings",
    "Settings",
    "SourcesATSConfig",
    "SourcesConfig",
    "load_settings",
]
