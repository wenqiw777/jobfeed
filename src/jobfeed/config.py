"""Configuration loading for the Phase 0 Jobfeed runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobfeed._config_loading import (
    apply_database_env_aliases,
    collect_env_overrides,
    load_toml_file,
    merge_dicts,
)
from jobfeed.config_sources import (
    SourcesATSConfig,
    SourcesConfig,
    SourcesIndeedConfig,
    SourcesLinkedInConfig,
    SourcesLinkedInGuestConfig,
    SourcesLinkedInSearchConfig,
    SourcesSpeedyApplyConfig,
)
from jobfeed.domain.filtering import HardFilters


class DBSettings(BaseModel):
    """Shared persistent SQLite file used by every normal runtime process."""

    model_config = ConfigDict(extra="forbid")

    path: Path = Path("data/jobfeed.sqlite")

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("SQLite db.path must not be empty")
        return value

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_url(cls, value: object) -> object:
        if isinstance(value, Mapping) and "url" in value:
            raise ValueError(
                "PostgreSQL runtime db.url is no longer supported; run "
                "`./bin/jobfeed migrate pg-to-sqlite` and configure [db].path"
            )
        return value


class LLMSettings(BaseModel):
    """LLM model and runtime limits used by evaluation services."""

    model_config = ConfigDict(extra="forbid")

    stage_a: str = "codex-cli/gpt-5.6-luna"
    stage_b: str = "codex-cli/gpt-5.6-sol"
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
    # Default per-stage cap when `evaluate` is run without --limit/--full.
    # The CLI flags still override this; 0 means "evaluate nothing by default".
    default_eval_limit: int = Field(default=150, ge=0)


class MLGateSettings(BaseModel):
    """Configuration for the XGBoost ML gate used in Phase 5 evaluation funnel."""

    model_config = ConfigDict(extra="forbid")

    model_dir: str = "models/ml_gate"
    model_version: str = "v20260601T170453Z"
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


class DigestSettings(BaseModel):
    """Digest output settings used by the digest CLI command."""

    model_config = ConfigDict(extra="forbid")

    # When set, `jobfeed digest` also writes today.md and YYYY-MM-DD.md here.
    output_dir: str | None = None


class ExecutionSettings(BaseModel):
    """Execution mode settings for local workflow orchestration."""

    model_config = ConfigDict(extra="forbid")

    default_runner: str = "in_process"


class ObservabilitySettings(BaseModel):
    """Logging, OTel tracing, and Sentry error-tracking settings."""

    model_config = ConfigDict(extra="forbid")

    log_level: str = "info"
    log_format: Literal["human", "json"] = "human"
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "jobfeed"
    sentry_dsn: str | None = None
    sentry_environment: str = "dev"


class Settings(BaseModel):
    """Validated top-level application settings."""

    model_config = ConfigDict(extra="forbid")

    db: DBSettings = Field(default_factory=DBSettings)
    digest: DigestSettings = Field(default_factory=DigestSettings)
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
    file_data = load_toml_file(config_path)
    env_data = collect_env_overrides(os.environ)
    apply_database_env_aliases(os.environ, env_data)
    merged = merge_dicts(file_data, env_data)
    settings = Settings.model_validate(merged)
    return _resolve_database_path(settings, config_path)


def _resolve_database_path(settings: Settings, config_path: Path | None) -> Settings:
    path = settings.db.path.expanduser()
    if not path.is_absolute() and config_path is not None:
        path = config_path.resolve().parent / path
    database = settings.db.model_copy(update={"path": path})
    return settings.model_copy(update={"db": database})


__all__ = [
    "DBSettings",
    "DigestSettings",
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
    "SourcesLinkedInGuestConfig",
    "SourcesLinkedInSearchConfig",
    "SourcesSpeedyApplyConfig",
    "load_settings",
]
