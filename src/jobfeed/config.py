"""Configuration loading for the Phase 0 Jobfeed runtime."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

CONFIG_ENV_PREFIX = "JOBFEED_"
ENV_NESTED_DELIMITER = "__"
DEFAULT_SQLITE_PATH = Path(".jobfeed-dev/dev.db")


class DBSettings(BaseModel):
    """Persistence backend settings validated after TOML and env merging."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["sqlite", "postgres"] = "sqlite"
    url: str | None = None
    sqlite_path: Path = DEFAULT_SQLITE_PATH


class LLMSettings(BaseModel):
    """LLM model and runtime limits used by evaluation services."""

    model_config = ConfigDict(extra="forbid")

    stage_a: str = "mock/stage-a"
    stage_b: str = "mock/stage-b"
    max_concurrent: int = 2
    timeout_s: float = 60.0


class ScoringSettings(BaseModel):
    """Scoring gates and Phase 0 call-budget controls."""

    model_config = ConfigDict(extra="forbid")

    stage_a_threshold: int = 60
    ml_gate_enabled: bool = False
    max_daily_score_calls: int = 100


class ExecutionSettings(BaseModel):
    """Execution mode settings for local workflow orchestration."""

    model_config = ConfigDict(extra="forbid")

    default_runner: str = "in_process"


class ObservabilitySettings(BaseModel):
    """Logging settings shared by CLI and service entry points."""

    model_config = ConfigDict(extra="forbid")

    log_level: str = "info"
    log_format: Literal["human", "json"] = "human"


class Settings(BaseModel):
    """Validated top-level application settings."""

    model_config = ConfigDict(extra="forbid")

    db: DBSettings = Field(default_factory=DBSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


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
    "DEFAULT_SQLITE_PATH",
    "DBSettings",
    "ExecutionSettings",
    "LLMSettings",
    "ObservabilitySettings",
    "ScoringSettings",
    "Settings",
    "load_settings",
]
