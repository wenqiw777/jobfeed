"""Unit tests for extended hard filters (company + location + date, legacy parity)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from jobfeed.config import HardFiltersSettings, Settings
from jobfeed.domain.filtering import HardFilters, apply_hard_filters
from jobfeed.domain.models import JobPosting
from tests.support.factories import FIXED_TIME, make_job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def _days_ago(n: int) -> datetime:
    return _NOW - timedelta(days=n)


def _fresh(days: int = 3) -> datetime:
    return _days_ago(days)


def _stale(days: int = 40) -> datetime:
    return _days_ago(days)


def _empty_filters() -> HardFilters:
    return HardFilters(
        title_blocklist=[],
        company_blocklist=[],
        location_allowlist=[],
        location_blocklist=[],
        posted_within_days=None,
        big_company_list=[],
        big_company_days=90,
    )


# ---------------------------------------------------------------------------
# 1. Company blocklist
# ---------------------------------------------------------------------------


def test_company_blocklist_blocks_on_substring() -> None:
    f = HardFilters(
        title_blocklist=[],
        company_blocklist=["agency"],
        location_allowlist=[],
        location_blocklist=[],
        posted_within_days=None,
        big_company_list=[],
        big_company_days=90,
    )
    reason = apply_hard_filters(make_job(company="Hiring Agency"), f)
    assert reason is not None
    assert "agency" in reason.lower()


def test_company_blocklist_case_insensitive() -> None:
    f = _empty_filters()
    f.company_blocklist = ["AGENCY"]
    reason = apply_hard_filters(make_job(company="hiring agency"), f)
    assert reason is not None


def test_company_blocklist_passes_clean() -> None:
    f = _empty_filters()
    f.company_blocklist = ["agency"]
    reason = apply_hard_filters(make_job(company="Acme Corp"), f)
    assert reason is None


# ---------------------------------------------------------------------------
# 2. Location allowlist
# ---------------------------------------------------------------------------


def test_location_allowlist_blocks_location_not_in_list() -> None:
    f = _empty_filters()
    f.location_allowlist = ["remote", "san francisco"]
    reason = apply_hard_filters(make_job(location="New York, NY"), f)
    assert reason is not None
    assert "allowlist" in reason


def test_location_allowlist_passes_matching_location() -> None:
    f = _empty_filters()
    f.location_allowlist = ["remote"]
    reason = apply_hard_filters(make_job(location="Remote"), f)
    assert reason is None


def test_location_allowlist_case_insensitive() -> None:
    f = _empty_filters()
    f.location_allowlist = ["Remote"]
    reason = apply_hard_filters(make_job(location="remote - us"), f)
    assert reason is None


def test_empty_location_passes_allowlist() -> None:
    """Empty/None location must pass even when allowlist is configured."""
    f = _empty_filters()
    f.location_allowlist = ["remote", "san francisco"]
    reason_empty = apply_hard_filters(make_job(location=""), f)
    assert reason_empty is None


def test_none_location_passes_allowlist() -> None:
    """None location must pass even when allowlist is configured."""
    f = _empty_filters()
    f.location_allowlist = ["remote"]
    job = JobPosting(
        platform="mock",
        canonical_id="no-loc",
        url="https://example.com/no-loc",
        title="Backend Engineer",
        company="Acme",
        location=None,  # type: ignore[arg-type]
        discovered_at=FIXED_TIME,
    )
    reason = apply_hard_filters(job, f)
    assert reason is None


# ---------------------------------------------------------------------------
# 3. Location blocklist
# ---------------------------------------------------------------------------


def test_location_blocklist_blocks_matching() -> None:
    f = _empty_filters()
    f.location_blocklist = ["new york"]
    reason = apply_hard_filters(make_job(location="New York, NY"), f)
    assert reason is not None
    assert "new york" in reason


def test_location_blocklist_passes_non_matching() -> None:
    f = _empty_filters()
    f.location_blocklist = ["new york"]
    reason = apply_hard_filters(make_job(location="San Francisco, CA"), f)
    assert reason is None


def test_empty_location_passes_blocklist() -> None:
    """Empty location must pass even when blocklist is configured."""
    f = _empty_filters()
    f.location_blocklist = ["new york"]
    reason = apply_hard_filters(make_job(location=""), f)
    assert reason is None


# ---------------------------------------------------------------------------
# 4. Freshness — basic posted_within_days
# ---------------------------------------------------------------------------

_DAYS_LIMIT = 30
_STALE_DAYS = 40
_FRESH_DAYS = 3
_BIG_COMPANY_DAYS = 90
_BORDERLINE_DAYS = 60
_VERY_STALE_DAYS = 100


def test_freshness_blocks_stale_job() -> None:
    f = _empty_filters()
    f.posted_within_days = _DAYS_LIMIT
    job = make_job(discovered_at=_stale(_STALE_DAYS))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is not None
    assert str(_DAYS_LIMIT) in reason


def test_freshness_passes_fresh_job() -> None:
    f = _empty_filters()
    f.posted_within_days = _DAYS_LIMIT
    job = make_job(discovered_at=_fresh(_FRESH_DAYS))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is None


def test_freshness_no_filter_when_none() -> None:
    f = _empty_filters()
    f.posted_within_days = None
    job = make_job(discovered_at=_days_ago(365))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is None


# ---------------------------------------------------------------------------
# 5. Big-company tiering
# ---------------------------------------------------------------------------


def test_big_company_uses_big_company_days() -> None:
    """A big-company match should use big_company_days, not posted_within_days."""
    f = _empty_filters()
    f.posted_within_days = _DAYS_LIMIT
    f.big_company_list = ["Google"]
    f.big_company_days = _BIG_COMPANY_DAYS
    # 60 days old: stale for posted_within_days=30 but fresh for big_company_days=90
    job = make_job(company="Google", discovered_at=_days_ago(_BORDERLINE_DAYS))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is None


def test_big_company_blocks_beyond_big_days() -> None:
    """A big-company job older than big_company_days should be blocked."""
    f = _empty_filters()
    f.posted_within_days = _DAYS_LIMIT
    f.big_company_list = ["Google"]
    f.big_company_days = _BIG_COMPANY_DAYS
    job = make_job(company="Google", discovered_at=_days_ago(_VERY_STALE_DAYS))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is not None
    assert str(_BIG_COMPANY_DAYS) in reason


def test_small_company_uses_posted_within_days() -> None:
    """A non-big-company job uses posted_within_days, not big_company_days."""
    f = _empty_filters()
    f.posted_within_days = _DAYS_LIMIT
    f.big_company_list = ["Google"]
    f.big_company_days = _BIG_COMPANY_DAYS
    # 60 days old: stale for posted_within_days=30
    job = make_job(company="Acme Startup", discovered_at=_days_ago(_BORDERLINE_DAYS))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is not None
    assert str(_DAYS_LIMIT) in reason


def test_big_company_substring_match_case_insensitive() -> None:
    """Big-company matching is case-insensitive substring."""
    f = _empty_filters()
    f.posted_within_days = _DAYS_LIMIT
    f.big_company_list = ["google"]
    f.big_company_days = _BIG_COMPANY_DAYS
    # 60 days old — should get big-company leniency
    job = make_job(company="Google LLC", discovered_at=_days_ago(_BORDERLINE_DAYS))
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is None


# ---------------------------------------------------------------------------
# 6. title_blocklist intentionally NOT applied in apply_hard_filters
# ---------------------------------------------------------------------------


def test_title_blocklist_not_applied() -> None:
    """title_blocklist must NOT be used by apply_hard_filters (moved to ML gate)."""
    f = _empty_filters()
    f.title_blocklist = ["senior", "staff", "principal"]
    job = make_job(title="Senior Software Engineer")
    reason = apply_hard_filters(job, f)
    assert reason is None, (
        "title_blocklist should NOT block jobs — that moved to the ML gate"
    )


# ---------------------------------------------------------------------------
# 7. Filter ordering — company checked before location
# ---------------------------------------------------------------------------


def test_company_blocked_before_location() -> None:
    """Company blocklist should fire before location check."""
    f = _empty_filters()
    f.company_blocklist = ["acme"]
    f.location_allowlist = ["remote"]
    # Location "New York" would fail the allowlist; company fires first
    job = make_job(company="Acme Corp", location="New York, NY")
    reason = apply_hard_filters(job, f)
    assert reason is not None
    assert "company" in reason.lower()


# ---------------------------------------------------------------------------
# 8. Config round-trip — HardFiltersSettings → HardFilters
# ---------------------------------------------------------------------------


def test_hard_filters_settings_to_domain() -> None:
    """HardFiltersSettings.to_domain() should produce a correct HardFilters."""
    settings = HardFiltersSettings(
        company_blocklist=["agency"],
        location_allowlist=["remote"],
        location_blocklist=["new york"],
        posted_within_days=_DAYS_LIMIT,
        big_company_list=["google"],
        big_company_days=_BIG_COMPANY_DAYS,
    )
    hf = settings.to_domain()
    assert hf.company_blocklist == ["agency"]
    assert hf.location_allowlist == ["remote"]
    assert hf.location_blocklist == ["new york"]
    assert hf.posted_within_days == _DAYS_LIMIT
    assert hf.big_company_list == ["google"]
    assert hf.big_company_days == _BIG_COMPANY_DAYS


def test_hard_filters_settings_accepts_title_blocklist() -> None:
    """A [hard_filters] config WITH title_blocklist must parse (back-compat)."""
    settings = HardFiltersSettings(title_blocklist=["senior"])
    assert settings.to_domain().title_blocklist == ["senior"]


def test_full_settings_parses_title_blocklist_section() -> None:
    """Top-level Settings must accept a [hard_filters] section with title_blocklist.

    A legacy/spec config.toml containing [hard_filters] title_blocklist = [...]
    must load and round-trip (not raise ValidationError under extra='forbid').
    """
    s = Settings.model_validate({"hard_filters": {"title_blocklist": ["senior"]}})
    assert s.hard_filters.title_blocklist == ["senior"]
    assert s.hard_filters.to_domain().title_blocklist == ["senior"]


def test_title_blocklist_from_settings_still_not_applied() -> None:
    """title_blocklist sourced from config must STILL NOT block a Senior job."""
    filters = HardFiltersSettings(title_blocklist=["senior"]).to_domain()
    assert filters.title_blocklist == ["senior"]
    reason = apply_hard_filters(make_job(title="Senior Software Engineer"), filters)
    assert reason is None, (
        "title_blocklist must NOT block jobs even when loaded from config"
    )


def test_hard_filters_settings_defaults_no_filter() -> None:
    """Default HardFiltersSettings should produce no-op HardFilters."""
    settings = HardFiltersSettings()
    hf = settings.to_domain()
    assert hf.company_blocklist == []
    assert hf.location_allowlist == []
    assert hf.location_blocklist == []
    assert hf.posted_within_days is None
    assert hf.big_company_list == []


def test_hard_filters_settings_unknown_key_raises() -> None:
    """extra='forbid' must prevent unknown config keys."""
    with pytest.raises(ValidationError):
        HardFiltersSettings(**{"typo_field": "bad"})  # type: ignore[call-overload]


def test_settings_has_hard_filters_field() -> None:
    """Top-level Settings should expose a hard_filters section."""
    s = Settings()
    assert hasattr(s, "hard_filters")


# ---------------------------------------------------------------------------
# 9. No-op — empty filters pass everything
# ---------------------------------------------------------------------------


def test_all_empty_filters_pass() -> None:
    f = _empty_filters()
    job = make_job(
        company="Evil Agency",
        location="Middle of Nowhere",
        discovered_at=_stale(365),
    )
    reason = apply_hard_filters(job, f, now=_NOW)
    assert reason is None


# ---------------------------------------------------------------------------
# 10. Validation — numeric bounds on day-count fields
# ---------------------------------------------------------------------------


def test_posted_within_days_rejects_negative() -> None:
    """posted_within_days must be >= 1; a negative value must raise ValidationError."""
    with pytest.raises(ValidationError):
        HardFiltersSettings(posted_within_days=-5)
