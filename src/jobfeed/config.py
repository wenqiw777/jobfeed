"""Configuration loading for the Phase 0 Jobfeed runtime."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jobfeed.config_sources import (
    SourcesATSConfig,
    SourcesConfig,
    SourcesIndeedConfig,
    SourcesLinkedInConfig,
    SourcesLinkedInJobSpyConfig,
    SourcesLinkedInSearchConfig,
    SourcesSpeedyApplyConfig,
)
from jobfeed.domain.filtering import HardFilters

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

    stage_a: str = "codex-cli/gpt-5.4-mini"
    stage_b: str = "codex-cli/gpt-5.5"
    codex_timeout_s: float = 60.0
    claude_timeout_s: float = 210.0
    openai_compat_base_url: str = "https://api.openai.com/v1"
    openai_compat_api_key_env: str = "OPENAI_API_KEY"
    openai_compat_timeout_s: float = 60.0
    max_concurrent: int = 4
    master_resume_path: str = "resume.example.md"
    preamble_personal_path: str | None = None
    max_daily_score_calls: int = Field(default=150, ge=0)
    max_daily_cost_usd: float = Field(default=10.0, ge=0)

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


class MLGateSettings(BaseModel):
    """Configuration for the XGBoost ML gate used in Phase 5 evaluation funnel."""

    model_config = ConfigDict(extra="forbid")

    model_dir: str = "models/ml_gate"
    # The fastembed embedder maps this legacy short name to its full Hugging
    # Face id ("sentence-transformers/all-MiniLM-L6-v2"); both forms work.
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_max_chars: int = Field(default=2000, gt=0)
    threshold_override: float | None = Field(default=None, ge=0, le=1)
    max_candidates: int = Field(default=5000, ge=1)


class HardFiltersSettings(BaseModel):
    """Hard filter settings mirroring HardFilters domain object.

    Empty defaults mean no filtering — a config file without a [hard_filters]
    section is equivalent to no filters at all.
    """

    model_config = ConfigDict(extra="forbid")

    title_blocklist: list[str] = Field(default_factory=list)
    company_blocklist: list[str] = Field(default_factory=list)
    location_allowlist: list[str] = Field(default_factory=list)
    location_blocklist: list[str] = Field(default_factory=list)
    posted_within_days: int | None = Field(default=None, ge=1)
    big_company_list: list[str] = Field(default_factory=list)
    big_company_days: int = Field(default=90, ge=1)

    def to_domain(self) -> HardFilters:
        """Build the pure domain HardFilters from this settings object.

        Returns:
            HardFilters domain object with values copied from this settings model.
        """
        return HardFilters(
            title_blocklist=list(self.title_blocklist),
            company_blocklist=list(self.company_blocklist),
            location_allowlist=list(self.location_allowlist),
            location_blocklist=list(self.location_blocklist),
            posted_within_days=self.posted_within_days,
            big_company_list=list(self.big_company_list),
            big_company_days=self.big_company_days,
        )


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
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    ml_gate: MLGateSettings = Field(default_factory=MLGateSettings)
    hard_filters: HardFiltersSettings = Field(default_factory=HardFiltersSettings)


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
        raw_path = key.removeprefix(CONFIG_ENV_PREFIX)
        if ENV_NESTED_DELIMITER not in raw_path:
            continue
        path = raw_path.lower().split(ENV_NESTED_DELIMITER)
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
    "HardFiltersSettings",
    "LLMSettings",
    "MLGateSettings",
    "ObservabilitySettings",
    "ScoringSettings",
    "Settings",
    "SourcesATSConfig",
    "SourcesConfig",
    "SourcesIndeedConfig",
    "SourcesLinkedInConfig",
    "SourcesLinkedInJobSpyConfig",
    "SourcesLinkedInSearchConfig",
    "SourcesSpeedyApplyConfig",
    "load_settings",
]
