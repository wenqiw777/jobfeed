"""Configuration models for job source adapters."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SourcesSpeedyApplyConfig(BaseModel):
    """Runtime limits and tuning knobs for the SpeedyApply source.

    ``search_urls`` lists the GitHub markdown job lists to scan; an empty list
    selects the built-in default README. List fields are TOML-only: the nested
    env setter stores a bare string at the leaf, which Pydantic will not coerce
    into a list, so ``JOBFEED_SOURCES__SPEEDYAPPLY__SEARCH_URLS`` is unsupported.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    search_urls: list[str] = Field(default_factory=list)
    max_concurrent: int = Field(default=10, ge=1)
    fetch_timeout_s: float = Field(default=30.0, gt=0)


class _JobSpySourceConfig(BaseModel):
    """Shared shape and guards for JobSpy-backed sources."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    search_urls: list[str] = Field(default_factory=list)
    max_jobs: int = Field(default=100, ge=1)
    hours_old: int | None = None

    @model_validator(mode="after")
    def _require_urls_when_enabled(self) -> _JobSpySourceConfig:
        """Fail loud if enabled with no usable URL."""
        if self.enabled and (
            not self.search_urls or any(not url.strip() for url in self.search_urls)
        ):
            raise ValueError(
                "JobSpy source is enabled but search_urls is empty or has a blank "
                "entry; JobSpy has no default search, so every entry must be a real "
                "URL. Fix search_urls or set enabled = false."
            )
        return self


class SourcesIndeedConfig(_JobSpySourceConfig):
    """Runtime limits and tuning knobs for the Indeed (JobSpy) source."""


class SourcesLinkedInJobSpyConfig(_JobSpySourceConfig):
    """Runtime limits and tuning knobs for the LinkedIn (JobSpy) source."""


class SourcesLinkedInSearchConfig(BaseModel):
    """One LinkedIn Playwright search URL with optional local budgets."""

    model_config = ConfigDict(extra="forbid")

    url: str
    max_jobs: int | None = Field(default=None, ge=1)
    group: str | None = None
    group_max_jobs: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _reject_blank_fields(self) -> SourcesLinkedInSearchConfig:
        """Reject blank URL/group values that would create empty searches."""
        if not self.url.strip():
            raise ValueError("LinkedIn search url must not be blank")
        if self.group is not None and not self.group.strip():
            raise ValueError("LinkedIn search group must not be blank")
        return self


class SourcesLinkedInConfig(BaseModel):
    """Runtime limits and profile paths for the LinkedIn Playwright source."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    search_urls: list[str | SourcesLinkedInSearchConfig] = Field(default_factory=list)
    max_jobs: int = Field(default=100, ge=1)
    headless: bool = True
    tier2_cap: int = Field(default=30, ge=0)
    profile_dir: str = "~/.cache/jobfeed/linkedin"
    lock_path: str = "~/.cache/jobfeed/enrich.lock"

    @model_validator(mode="after")
    def _require_urls_when_enabled(self) -> SourcesLinkedInConfig:
        """Fail loud if enabled with no usable LinkedIn search URL."""
        if self.enabled and (
            not self.search_urls
            or any(_is_blank_linkedin_search(v) for v in self.search_urls)
        ):
            raise ValueError(
                "LinkedIn source is enabled but search_urls is empty or has a "
                "blank entry; set at least one search URL or set enabled = false."
            )
        return self


class SourcesConfig(BaseModel):
    """Container for all job-data source configurations."""

    model_config = ConfigDict(extra="forbid")

    ats: SourcesATSConfig = Field(default_factory=SourcesATSConfig)
    speedyapply: SourcesSpeedyApplyConfig = Field(
        default_factory=SourcesSpeedyApplyConfig
    )
    indeed: SourcesIndeedConfig = Field(default_factory=SourcesIndeedConfig)
    linkedin_jobspy: SourcesLinkedInJobSpyConfig = Field(
        default_factory=SourcesLinkedInJobSpyConfig
    )
    linkedin: SourcesLinkedInConfig = Field(default_factory=SourcesLinkedInConfig)


def _is_blank_linkedin_search(value: str | SourcesLinkedInSearchConfig) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return not value.url.strip()


__all__ = [
    "SourcesATSConfig",
    "SourcesConfig",
    "SourcesIndeedConfig",
    "SourcesLinkedInConfig",
    "SourcesLinkedInJobSpyConfig",
    "SourcesLinkedInSearchConfig",
    "SourcesSpeedyApplyConfig",
]
