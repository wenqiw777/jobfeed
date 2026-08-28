"""Unit tests for configuration loading and observability setup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfeed.config import (
    LLMSettings,
    MLGateSettings,
    ObservabilitySettings,
    ScoringSettings,
    Settings,
    SourcesATSConfig,
    SourcesConfig,
    SourcesIndeedConfig,
    SourcesLinkedInConfig,
    SourcesLinkedInGuestConfig,
    SourcesLinkedInSearchConfig,
    SourcesSpeedyApplyConfig,
    load_settings,
)
from jobfeed.observability import bind_run_id, configure_logging, get_logger

DEFAULT_STAGE_A_THRESHOLD = 60
ENV_MAX_CONCURRENT = 7
REPO_ROOT = Path(__file__).resolve().parents[2]

# LLM config default values (mirrors LLMSettings defaults)
LLM_DEFAULT_CODEX_TIMEOUT_S = 60.0
LLM_DEFAULT_CLAUDE_TIMEOUT_S = 210.0
LLM_DEFAULT_BEDROCK_TIMEOUT_S = 180.0
LLM_DEFAULT_MAX_CONCURRENT = 4
LLM_DEFAULT_MAX_DAILY_SCORE_CALLS = 150
LLM_DEFAULT_MAX_DAILY_COST_USD = 10.0

# ATS config default values (mirrors SourcesATSConfig defaults)
ATS_DEFAULT_MAX_CONCURRENT = 10
ATS_DEFAULT_MAX_JOBS = 1000
ATS_DEFAULT_PROBE_TTL_DAYS = 7
ATS_DEFAULT_FAILURE_THRESHOLD = 3
ATS_DEFAULT_PROBE_TIMEOUT_S = 5.0
ATS_DEFAULT_SCAN_TIMEOUT_S = 30.0
ATS_ENV_MAX_CONCURRENT = 5

# Phase 4a source config default values (mirror the new SourcesConfig models)
SPEEDYAPPLY_DEFAULT_MAX_CONCURRENT = 10
SPEEDYAPPLY_DEFAULT_MAX_JOBS = 1000
SPEEDYAPPLY_DEFAULT_FETCH_TIMEOUT_S = 30.0
JOBSPY_DEFAULT_MAX_JOBS = 100
JOBSPY_DEFAULT_MAX_CONCURRENT = 2
JOBSPY_DEFAULT_TIMEOUT_S = 60.0
JOBSPY_DEFAULT_COUNTRY_INDEED = "usa"
JOBSPY_DEFAULT_REPEAT = 1
INDEED_ENV_MAX_JOBS = 50
INDEED_TOML_MAX_JOBS = 25
INDEED_TOML_HOURS_OLD = 48
LINKEDIN_DEFAULT_PROFILE_DIR = "~/.cache/jobfeed/linkedin"
LINKEDIN_DEFAULT_LOCK_PATH = "~/.cache/jobfeed/enrich.lock"
LINKEDIN_DEFAULT_TIER2_CAP = 30
LINKEDIN_TOML_MAX_JOBS = 40
LINKEDIN_TOML_GROUP_MAX_JOBS = 12
LINKEDIN_TOML_TIER2_CAP = 5

# LinkedIn guest source defaults (mirror SourcesLinkedInGuestConfig)
LINKEDIN_GUEST_DEFAULT_MAX_JOBS = 1000
LINKEDIN_GUEST_DEFAULT_PACING_S = 1.0
LINKEDIN_GUEST_DEFAULT_ENRICH_BATCH_LIMIT = 500
LINKEDIN_GUEST_DEFAULT_TIMEOUT_S = 15.0
LINKEDIN_GUEST_TOML_MAX_JOBS = 250
LINKEDIN_GUEST_TOML_PACING_S = 2.5
LINKEDIN_GUEST_TOML_ENRICH_BATCH_LIMIT = 100
LINKEDIN_GUEST_TOML_TIMEOUT_S = 20.0


def test_load_settings_returns_defaults_without_config_file() -> None:
    """Omitting config should fall back to the repo-local SQLite path."""
    settings = load_settings()

    assert settings.db.path == Path("data/jobfeed.sqlite")
    assert settings.llm.stage_a == "codex-cli/gpt-5.6-luna"


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

    assert settings.db.path == REPO_ROOT / "data/jobfeed.sqlite"
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
    config_path.write_text('[db]\npath = "file.sqlite"\n', encoding="utf-8")
    monkeypatch.setenv("JOBFEED_DB__PATH", "env.sqlite")

    settings = load_settings(config_path)

    assert settings.db.path == tmp_path / "env.sqlite"


def test_load_settings_flat_db_path_alias_maps_to_db_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flat ``JOBFEED_DB_PATH`` alias maps to ``db.path`` and validates.

    Regression: ``_collect_env_overrides`` splits only on ``__``, so the flat
    var otherwise lands as a forbidden top-level ``db_path`` key.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    path = "/data/jobfeed.sqlite"
    monkeypatch.setenv("JOBFEED_DB_PATH", path)

    settings = load_settings()

    assert settings.db.path == Path(path)


def test_load_settings_ignores_flat_compose_plumbing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flat non-alias JOBFEED vars are Docker plumbing, not app config."""
    monkeypatch.setenv("JOBFEED_POSTGRES_PORT", "55432")

    settings = load_settings()

    assert settings.db.path == Path("data/jobfeed.sqlite")


def test_load_settings_nested_db_path_beats_flat_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nested ``JOBFEED_DB__PATH`` form takes precedence over the flat alias.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    monkeypatch.setenv("JOBFEED_DB_PATH", "flat.sqlite")
    monkeypatch.setenv("JOBFEED_DB__PATH", "nested.sqlite")

    settings = load_settings()

    assert settings.db.path == Path("nested.sqlite")


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

    assert cfg.stage_a == "codex-cli/gpt-5.6-luna"
    assert cfg.stage_b == "codex-cli/gpt-5.6-sol"
    assert cfg.codex_timeout_s == LLM_DEFAULT_CODEX_TIMEOUT_S
    assert cfg.claude_timeout_s == LLM_DEFAULT_CLAUDE_TIMEOUT_S
    assert cfg.bedrock_region == "us-east-1"
    assert cfg.bedrock_profile is None
    assert cfg.bedrock_timeout_s == LLM_DEFAULT_BEDROCK_TIMEOUT_S
    assert cfg.max_concurrent == LLM_DEFAULT_MAX_CONCURRENT
    assert cfg.master_resume_path == "resume.example.md"
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
        "bedrock/us.anthropic.claude-sonnet-5",
        "mock/stage-a",
    ]:
        cfg = LLMSettings(stage_a=spec)
        assert cfg.stage_a == spec


def test_llm_settings_rejects_extra_fields() -> None:
    """extra='forbid' should reject unknown fields."""
    with pytest.raises(ValidationError):
        LLMSettings(nonexistent_field="value")  # type: ignore[call-arg]


def test_llm_settings_rejects_negative_budget_limits() -> None:
    """Daily LLM budget settings should allow zero but reject negatives."""
    LLMSettings(max_daily_score_calls=0, max_daily_cost_usd=0.0)

    with pytest.raises(ValidationError):
        LLMSettings(max_daily_score_calls=-1)
    with pytest.raises(ValidationError):
        LLMSettings(max_daily_cost_usd=-0.01)


def test_llm_settings_rejects_duplicate_azure_deployment_prices() -> None:
    """Each Azure deployment alias must resolve to exactly one price record."""
    duplicate = {
        "deployment": "quick-prod",
        "base_model": "gpt-4.1-mini",
        "input_usd_per_million": 0.4,
        "output_usd_per_million": 1.6,
    }

    with pytest.raises(ValidationError, match="duplicate Azure deployment pricing"):
        LLMSettings(azure_deployment_pricing=[duplicate, duplicate])


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


# --- Phase 4a source config tests (speedyapply / indeed) ---


def test_settings_exposes_phase4a_source_defaults() -> None:
    """Settings.sources should expose the Phase 4a sources, all disabled."""
    sources = load_settings().sources

    assert isinstance(sources.speedyapply, SourcesSpeedyApplyConfig)
    assert isinstance(sources.indeed, SourcesIndeedConfig)

    assert sources.speedyapply.enabled is False
    assert sources.indeed.enabled is False

    assert sources.speedyapply.search_urls == []
    assert sources.speedyapply.max_jobs == SPEEDYAPPLY_DEFAULT_MAX_JOBS
    assert sources.speedyapply.max_concurrent == SPEEDYAPPLY_DEFAULT_MAX_CONCURRENT
    assert sources.speedyapply.fetch_timeout_s == SPEEDYAPPLY_DEFAULT_FETCH_TIMEOUT_S

    assert sources.indeed.search_urls == []
    assert sources.indeed.max_jobs == JOBSPY_DEFAULT_MAX_JOBS
    assert sources.indeed.hours_old is None
    assert sources.indeed.max_concurrent == JOBSPY_DEFAULT_MAX_CONCURRENT
    assert sources.indeed.timeout_s == JOBSPY_DEFAULT_TIMEOUT_S
    assert sources.indeed.country_indeed == JOBSPY_DEFAULT_COUNTRY_INDEED
    assert sources.indeed.repeat == JOBSPY_DEFAULT_REPEAT

    assert sources.ats.max_jobs == ATS_DEFAULT_MAX_JOBS
    assert sources.ats.title_keywords == []


def test_source_total_limits_reject_zero() -> None:
    """Every source-level scan limit must retain at least one job."""
    with pytest.raises(ValidationError):
        SourcesATSConfig(max_jobs=0)
    with pytest.raises(ValidationError):
        SourcesSpeedyApplyConfig(max_jobs=0)


def test_sources_derive_ats_target_titles_from_existing_search_urls() -> None:
    """Older configs gain ATS relevance terms without rerunning onboarding."""
    sources = SourcesConfig(
        indeed=SourcesIndeedConfig(
            enabled=True,
            search_urls=[
                "https://www.indeed.com/jobs?q=Backend+Software+Engineer&l=United+States"
            ],
        ),
        linkedin_guest=SourcesLinkedInGuestConfig(
            enabled=True,
            search_urls=[
                "https://www.linkedin.com/jobs/search/?keywords=AI%2FLLM+Engineer+Intern"
            ],
        ),
    )

    assert sources.ats.title_keywords == [
        "Backend Software Engineer",
        "AI/LLM Engineer Intern",
    ]


def test_settings_exposes_phase4b_linkedin_defaults() -> None:
    """Settings.sources should expose the Playwright LinkedIn source disabled."""
    linkedin = load_settings().sources.linkedin

    assert isinstance(linkedin, SourcesLinkedInConfig)
    assert linkedin.enabled is False
    assert linkedin.search_urls == []
    assert linkedin.max_jobs == JOBSPY_DEFAULT_MAX_JOBS
    assert linkedin.headless is True
    assert linkedin.tier2_cap == LINKEDIN_DEFAULT_TIER2_CAP
    assert linkedin.profile_dir == LINKEDIN_DEFAULT_PROFILE_DIR
    assert linkedin.lock_path == LINKEDIN_DEFAULT_LOCK_PATH


def test_load_settings_parses_phase4b_linkedin_section(tmp_path: Path) -> None:
    """load_settings should populate [sources.linkedin] from TOML."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[sources.linkedin]\n"
        "enabled = true\n"
        "search_urls = [\n"
        '  { url = "https://linkedin.test/jobs?keywords=intern", '
        f'max_jobs = {LINKEDIN_TOML_MAX_JOBS}, group = "fall", '
        f"group_max_jobs = {LINKEDIN_TOML_GROUP_MAX_JOBS} }}\n"
        "]\n"
        f"max_jobs = {LINKEDIN_TOML_MAX_JOBS}\n"
        "headless = false\n"
        f"tier2_cap = {LINKEDIN_TOML_TIER2_CAP}\n"
        'profile_dir = "/tmp/jobfeed-li-profile"\n'
        'lock_path = "/tmp/jobfeed-li.lock"\n',
        encoding="utf-8",
    )

    linkedin = load_settings(config_path).sources.linkedin

    assert linkedin.enabled is True
    assert linkedin.max_jobs == LINKEDIN_TOML_MAX_JOBS
    assert linkedin.headless is False
    assert linkedin.tier2_cap == LINKEDIN_TOML_TIER2_CAP
    assert linkedin.profile_dir == "/tmp/jobfeed-li-profile"
    assert linkedin.lock_path == "/tmp/jobfeed-li.lock"
    assert len(linkedin.search_urls) == 1
    search = linkedin.search_urls[0]
    assert isinstance(search, SourcesLinkedInSearchConfig)
    assert search.url == "https://linkedin.test/jobs?keywords=intern"
    assert search.max_jobs == LINKEDIN_TOML_MAX_JOBS
    assert search.group == "fall"
    assert search.group_max_jobs == LINKEDIN_TOML_GROUP_MAX_JOBS


def test_load_settings_parses_phase4a_source_sections(tmp_path: Path) -> None:
    """load_settings should populate each new [sources.*] block from TOML.

    Args:
        tmp_path: Temporary directory for a synthetic config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[sources.speedyapply]\n"
        "enabled = true\n"
        'search_urls = ["https://example.test/jobs.md"]\n'
        "[sources.indeed]\n"
        "enabled = true\n"
        'search_urls = ["https://example.test/indeed"]\n'
        f"max_jobs = {INDEED_TOML_MAX_JOBS}\n"
        f"hours_old = {INDEED_TOML_HOURS_OLD}\n"
        'country_indeed = "canada"\n',
        encoding="utf-8",
    )

    sources = load_settings(config_path).sources

    assert sources.speedyapply.enabled is True
    assert sources.speedyapply.search_urls == ["https://example.test/jobs.md"]
    assert sources.indeed.enabled is True
    assert sources.indeed.search_urls == ["https://example.test/indeed"]
    assert sources.indeed.max_jobs == INDEED_TOML_MAX_JOBS
    assert sources.indeed.hours_old == INDEED_TOML_HOURS_OLD
    assert sources.indeed.country_indeed == "canada"


def test_sources_speedyapply_config_rejects_unknown_key() -> None:
    """An unknown key in [sources.speedyapply] should fail (extra='forbid')."""
    with pytest.raises(ValidationError):
        SourcesSpeedyApplyConfig(unknown_key="value")  # type: ignore[call-arg]


def test_speedyapply_config_rejects_enabled_without_search_urls() -> None:
    """Enabled SpeedyApply must name the cycle/list URLs explicitly."""
    with pytest.raises(ValidationError):
        SourcesSpeedyApplyConfig(enabled=True)


def test_sources_indeed_config_rejects_unknown_key() -> None:
    """An unknown key in [sources.indeed] should fail (extra='forbid')."""
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(unknown_key="value")  # type: ignore[call-arg]


def test_sources_linkedin_config_rejects_unknown_key() -> None:
    """An unknown key in [sources.linkedin] should fail (extra='forbid')."""
    with pytest.raises(ValidationError):
        SourcesLinkedInConfig(unknown_key="value")  # type: ignore[call-arg]


def test_indeed_config_rejects_enabled_without_search_urls() -> None:
    """Enabled Indeed with no search_urls fails (JobSpy has no default search)."""
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(enabled=True)


def test_jobspy_config_allows_disabled_without_search_urls() -> None:
    """A disabled JobSpy source needs no search_urls — the default resting state."""
    assert SourcesIndeedConfig(enabled=False).search_urls == []
    assert SourcesIndeedConfig().enabled is False


def test_jobspy_config_allows_enabled_with_search_urls() -> None:
    """An enabled JobSpy source with at least one search_url validates."""
    cfg = SourcesIndeedConfig(enabled=True, search_urls=["https://x/jobs"])
    assert cfg.enabled is True
    assert cfg.search_urls == ["https://x/jobs"]


def test_linkedin_config_rejects_enabled_without_search_urls() -> None:
    """Enabled Playwright LinkedIn with no search_urls fails loud."""
    with pytest.raises(ValidationError):
        SourcesLinkedInConfig(enabled=True)


def test_linkedin_config_allows_string_and_structured_search_urls() -> None:
    """Playwright LinkedIn supports plain URLs plus per-search limits."""
    cfg = SourcesLinkedInConfig(
        enabled=True,
        search_urls=[
            "https://linkedin.test/jobs?keywords=swe",
            {
                "url": "https://linkedin.test/jobs?keywords=fall+intern",
                "max_jobs": LINKEDIN_TOML_MAX_JOBS,
                "group": "fall",
                "group_max_jobs": LINKEDIN_TOML_GROUP_MAX_JOBS,
            },
        ],
    )

    assert cfg.search_urls[0] == "https://linkedin.test/jobs?keywords=swe"
    search = cfg.search_urls[1]
    assert isinstance(search, SourcesLinkedInSearchConfig)
    assert search.max_jobs == LINKEDIN_TOML_MAX_JOBS
    assert search.group_max_jobs == LINKEDIN_TOML_GROUP_MAX_JOBS


def test_jobspy_config_rejects_blank_search_urls() -> None:
    """Enabled JobSpy with ANY blank/whitespace search_url entry is rejected."""
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(enabled=True, search_urls=["", "   "])
    # A mixed list with one real URL and one blank entry is still rejected:
    # the blank would otherwise be scraped as garbage.
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(enabled=True, search_urls=["   ", "https://valid.test/q"])


def test_sources_speedyapply_config_rejects_max_concurrent_zero() -> None:
    """speedyapply.max_concurrent=0 should fail validation (ge=1 constraint)."""
    with pytest.raises(ValidationError):
        SourcesSpeedyApplyConfig(max_concurrent=0)


def test_sources_indeed_config_rejects_max_jobs_zero() -> None:
    """indeed.max_jobs=0 should fail Pydantic validation (ge=1 constraint)."""
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(max_jobs=0)


def test_jobspy_config_rejects_nonpositive_timeout_or_concurrency() -> None:
    """JobSpy timeout/concurrency are runtime safety bounds."""
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(timeout_s=0)
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(max_concurrent=0)


def test_indeed_config_rejects_blank_country() -> None:
    """Indeed country must be explicit and non-blank when configured."""
    with pytest.raises(ValidationError):
        SourcesIndeedConfig(country_indeed=" ")


def test_sources_linkedin_config_rejects_nonpositive_limits() -> None:
    """Playwright LinkedIn source limits should be positive where required."""
    with pytest.raises(ValidationError):
        SourcesLinkedInConfig(max_jobs=0)
    with pytest.raises(ValidationError):
        SourcesLinkedInConfig(tier2_cap=-1)


def test_load_settings_env_overrides_indeed_max_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scalar JOBFEED_SOURCES__INDEED__MAX_JOBS env var should override config.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    monkeypatch.setenv("JOBFEED_SOURCES__INDEED__MAX_JOBS", str(INDEED_ENV_MAX_JOBS))

    settings = load_settings()

    assert settings.sources.indeed.max_jobs == INDEED_ENV_MAX_JOBS


def test_load_settings_env_enable_speedyapply_without_urls_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling SpeedyApply by env alone fails because list URLs are TOML-only.

    Args:
        monkeypatch: Pytest helper used to set scoped environment variables.
    """
    monkeypatch.setenv("JOBFEED_SOURCES__SPEEDYAPPLY__ENABLED", "true")

    with pytest.raises(ValidationError):
        load_settings()


# --- LinkedIn guest source config tests (sources.linkedin_guest) ---


def test_settings_exposes_linkedin_guest_defaults() -> None:
    """Settings.sources should expose the guest LinkedIn source disabled."""
    guest = load_settings().sources.linkedin_guest

    assert isinstance(guest, SourcesLinkedInGuestConfig)
    assert guest.enabled is False
    assert guest.search_urls == []
    assert guest.max_jobs == LINKEDIN_GUEST_DEFAULT_MAX_JOBS
    assert guest.pacing_s == LINKEDIN_GUEST_DEFAULT_PACING_S
    assert guest.enrich_batch_limit == LINKEDIN_GUEST_DEFAULT_ENRICH_BATCH_LIMIT
    assert guest.proxies is None
    assert guest.timeout_s == LINKEDIN_GUEST_DEFAULT_TIMEOUT_S


def test_sources_linkedin_guest_config_rejects_unknown_key() -> None:
    """An unknown key in [sources.linkedin_guest] should fail (extra='forbid')."""
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(unknown_key="value")  # type: ignore[call-arg]


def test_linkedin_guest_config_rejects_enabled_without_search_urls() -> None:
    """Enabled guest LinkedIn with no search_urls fails loud."""
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(enabled=True)


def test_linkedin_guest_config_rejects_blank_search_urls() -> None:
    """Enabled guest LinkedIn with ANY blank/whitespace search_url is rejected."""
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(enabled=True, search_urls=["", "   "])
    # A mixed list with one real URL and one blank entry is still rejected.
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(
            enabled=True, search_urls=["   ", "https://valid.test/q"]
        )


def test_linkedin_guest_config_allows_enabled_with_search_urls() -> None:
    """An enabled guest source with at least one search_url validates."""
    cfg = SourcesLinkedInGuestConfig(
        enabled=True,
        search_urls=["https://linkedin.test/jobs/search?keywords=intern"],
    )

    assert cfg.enabled is True
    assert cfg.search_urls == ["https://linkedin.test/jobs/search?keywords=intern"]


def test_linkedin_guest_config_allows_disabled_without_search_urls() -> None:
    """A disabled guest source needs no search_urls — the default resting state."""
    assert SourcesLinkedInGuestConfig().search_urls == []
    assert SourcesLinkedInGuestConfig(enabled=False).enabled is False


def test_sources_linkedin_guest_config_rejects_nonpositive_limits() -> None:
    """Guest source numeric knobs should enforce their lower bounds."""
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(max_jobs=0)
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(pacing_s=0)
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(enrich_batch_limit=0)
    with pytest.raises(ValidationError):
        SourcesLinkedInGuestConfig(timeout_s=0)


def test_linkedin_guest_config_normalizes_blank_proxies_to_none() -> None:
    """A blank proxies string means "no proxy"; real values are stripped/kept."""
    assert SourcesLinkedInGuestConfig(proxies="  ").proxies is None
    assert SourcesLinkedInGuestConfig(proxies="http://u:p@h:1").proxies == (
        "http://u:p@h:1"
    )


def test_load_settings_parses_linkedin_guest_section(tmp_path: Path) -> None:
    """load_settings should populate [sources.linkedin_guest] from TOML.

    Args:
        tmp_path: Temporary directory for a synthetic config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[sources.linkedin_guest]\n"
        "enabled = true\n"
        'search_urls = ["https://linkedin.test/jobs/search?keywords=intern"]\n'
        f"max_jobs = {LINKEDIN_GUEST_TOML_MAX_JOBS}\n"
        f"pacing_s = {LINKEDIN_GUEST_TOML_PACING_S}\n"
        f"enrich_batch_limit = {LINKEDIN_GUEST_TOML_ENRICH_BATCH_LIMIT}\n"
        'proxies = "http://proxy.test:8080"\n'
        f"timeout_s = {LINKEDIN_GUEST_TOML_TIMEOUT_S}\n",
        encoding="utf-8",
    )

    guest = load_settings(config_path).sources.linkedin_guest

    assert guest.enabled is True
    assert guest.search_urls == ["https://linkedin.test/jobs/search?keywords=intern"]
    assert guest.max_jobs == LINKEDIN_GUEST_TOML_MAX_JOBS
    assert guest.pacing_s == LINKEDIN_GUEST_TOML_PACING_S
    assert guest.enrich_batch_limit == LINKEDIN_GUEST_TOML_ENRICH_BATCH_LIMIT
    assert guest.proxies == "http://proxy.test:8080"
    assert guest.timeout_s == LINKEDIN_GUEST_TOML_TIMEOUT_S


# --- Phase 5 MLGateSettings tests ---

ML_GATE_DEFAULT_MODEL_DIR = "models/ml_gate"
ML_GATE_DEFAULT_MODEL_VERSION = "v20260601T170453Z"
ML_GATE_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
ML_GATE_DEFAULT_EMBEDDING_MAX_CHARS = 2000
ML_GATE_DEFAULT_MAX_CANDIDATES = 5000

# TOML-override values for test_load_settings_parses_ml_gate_section
ML_GATE_TOML_EMBEDDING_MAX_CHARS = 3000
ML_GATE_TOML_THRESHOLD_OVERRIDE = 0.6
ML_GATE_TOML_MAX_CANDIDATES = 1000


def test_ml_gate_settings_defaults() -> None:
    """MLGateSettings should expose all five fields with correct defaults."""
    cfg = MLGateSettings()

    assert cfg.model_dir == ML_GATE_DEFAULT_MODEL_DIR
    assert cfg.model_version == ML_GATE_DEFAULT_MODEL_VERSION
    assert cfg.embedding_model == ML_GATE_DEFAULT_EMBEDDING_MODEL
    assert cfg.embedding_max_chars == ML_GATE_DEFAULT_EMBEDDING_MAX_CHARS
    assert cfg.threshold_override is None
    assert cfg.max_candidates == ML_GATE_DEFAULT_MAX_CANDIDATES


def test_ml_gate_settings_rejects_unknown_key() -> None:
    """MLGateSettings with extra='forbid' should reject unknown keys."""
    with pytest.raises(ValidationError):
        MLGateSettings(nonexistent_key="value")  # type: ignore[call-arg]


def test_scoring_ml_gate_enabled_defaults_false() -> None:
    """ScoringSettings.ml_gate_enabled should default to False."""
    cfg = ScoringSettings()

    assert cfg.ml_gate_enabled is False


def test_settings_exposes_ml_gate_field() -> None:
    """Settings.ml_gate should be an MLGateSettings instance with defaults."""
    settings = load_settings()

    assert isinstance(settings.ml_gate, MLGateSettings)
    assert settings.ml_gate.model_dir == ML_GATE_DEFAULT_MODEL_DIR
    assert settings.ml_gate.model_version == ML_GATE_DEFAULT_MODEL_VERSION
    assert settings.ml_gate.embedding_model == ML_GATE_DEFAULT_EMBEDDING_MODEL
    assert settings.ml_gate.embedding_max_chars == ML_GATE_DEFAULT_EMBEDDING_MAX_CHARS
    assert settings.ml_gate.threshold_override is None
    assert settings.ml_gate.max_candidates == ML_GATE_DEFAULT_MAX_CANDIDATES


def test_ml_gate_settings_rejects_embedding_max_chars_zero() -> None:
    """embedding_max_chars must be >0 (gt=0 constraint)."""
    with pytest.raises(ValidationError):
        MLGateSettings(embedding_max_chars=0)


def test_ml_gate_settings_rejects_embedding_max_chars_negative() -> None:
    """embedding_max_chars must be >0; negative values are also rejected."""
    with pytest.raises(ValidationError):
        MLGateSettings(embedding_max_chars=-1)


def test_ml_gate_settings_rejects_threshold_override_below_zero() -> None:
    """threshold_override must be in [0, 1]; values below 0 are rejected."""
    with pytest.raises(ValidationError):
        MLGateSettings(threshold_override=-0.01)


def test_ml_gate_settings_rejects_threshold_override_above_one() -> None:
    """threshold_override must be in [0, 1]; values above 1 are rejected."""
    with pytest.raises(ValidationError):
        MLGateSettings(threshold_override=1.01)


def test_ml_gate_settings_accepts_threshold_override_boundary_values() -> None:
    """threshold_override accepts exactly 0.0 and 1.0 (inclusive bounds)."""
    cfg_low = MLGateSettings(threshold_override=0.0)
    cfg_high = MLGateSettings(threshold_override=1.0)

    assert cfg_low.threshold_override == 0.0
    assert cfg_high.threshold_override == 1.0


def test_ml_gate_settings_rejects_max_candidates_zero() -> None:
    """max_candidates must be >=1; zero is rejected."""
    with pytest.raises(ValidationError):
        MLGateSettings(max_candidates=0)


def test_load_settings_parses_ml_gate_section(tmp_path: Path) -> None:
    """load_settings should populate [ml_gate] fields from TOML."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[ml_gate]\n"
        'model_dir = "models/custom_gate"\n'
        'model_version = "v20990101T000000Z"\n'
        'embedding_model = "all-mpnet-base-v2"\n'
        f"embedding_max_chars = {ML_GATE_TOML_EMBEDDING_MAX_CHARS}\n"
        f"threshold_override = {ML_GATE_TOML_THRESHOLD_OVERRIDE}\n"
        f"max_candidates = {ML_GATE_TOML_MAX_CANDIDATES}\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.ml_gate.model_dir == "models/custom_gate"
    assert settings.ml_gate.model_version == "v20990101T000000Z"
    assert settings.ml_gate.embedding_model == "all-mpnet-base-v2"
    assert settings.ml_gate.embedding_max_chars == ML_GATE_TOML_EMBEDDING_MAX_CHARS
    assert settings.ml_gate.threshold_override == ML_GATE_TOML_THRESHOLD_OVERRIDE
    assert settings.ml_gate.max_candidates == ML_GATE_TOML_MAX_CANDIDATES


# --- Phase 9 ObservabilitySettings OTel/Sentry tests ---


def test_observability_settings_defaults() -> None:
    """ObservabilitySettings should expose correct defaults for all fields."""
    cfg = ObservabilitySettings()

    assert cfg.log_level == "info"
    assert cfg.log_format == "human"
    assert cfg.otel_enabled is False
    assert cfg.otel_endpoint == "http://localhost:4317"
    assert cfg.otel_service_name == "jobfeed"
    assert cfg.sentry_dsn is None
    assert cfg.sentry_environment == "dev"


def test_observability_settings_accepts_otel_enabled() -> None:
    """Settings should validate when observability.otel_enabled is True."""
    settings = Settings(observability={"otel_enabled": True})

    assert settings.observability.otel_enabled is True


def test_observability_settings_rejects_unknown_field() -> None:
    """Settings should reject unknown observability fields (extra='forbid')."""
    with pytest.raises(ValidationError):
        Settings(observability={"otel_enabled": True, "unknown_field": "x"})


def test_observability_settings_rejects_unknown_key_directly() -> None:
    """ObservabilitySettings rejects extra keys when constructed directly."""
    with pytest.raises(ValidationError):
        ObservabilitySettings(unknown_key="value")  # type: ignore[call-arg]


def test_observability_settings_accepts_custom_values() -> None:
    """ObservabilitySettings should accept all custom field values."""
    cfg = ObservabilitySettings(
        otel_enabled=True,
        otel_endpoint="http://jaeger:4317",
        otel_service_name="jobfeed-prod",
        sentry_dsn="https://key@sentry.io/123",
        sentry_environment="production",
    )

    assert cfg.otel_enabled is True
    assert cfg.otel_endpoint == "http://jaeger:4317"
    assert cfg.otel_service_name == "jobfeed-prod"
    assert cfg.sentry_dsn == "https://key@sentry.io/123"
    assert cfg.sentry_environment == "production"
