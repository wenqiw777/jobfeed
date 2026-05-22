"""Unit tests for deterministic hard filters."""

from __future__ import annotations

from jobfeed.domain.filtering import HardFilters, apply_hard_filters
from tests.support.factories import make_job


def test_apply_hard_filters_blocks_title_word() -> None:
    """Title blocklist matches should return a filter reason."""
    filters = HardFilters(title_blocklist=["senior"], company_blocklist=[])

    reason = apply_hard_filters(make_job(title="Senior Backend Engineer"), filters)

    assert reason == 'title contains "senior"'


def test_apply_hard_filters_blocks_company_word() -> None:
    """Company blocklist matches should return a filter reason."""
    filters = HardFilters(title_blocklist=[], company_blocklist=["agency"])

    reason = apply_hard_filters(make_job(company="Hiring Agency"), filters)

    assert reason == 'company contains "agency"'


def test_apply_hard_filters_matches_case_insensitively() -> None:
    """Blocklist matching should ignore title and company casing."""
    filters = HardFilters(title_blocklist=["SENIOR"], company_blocklist=[])

    reason = apply_hard_filters(make_job(title="senior backend engineer"), filters)

    assert reason == 'title contains "SENIOR"'


def test_apply_hard_filters_matches_partial_words() -> None:
    """Phase 0 blocklists intentionally use substring matching."""
    filters = HardFilters(title_blocklist=["sales"], company_blocklist=[])

    reason = apply_hard_filters(make_job(title="Presales Engineer"), filters)

    assert reason == 'title contains "sales"'


def test_apply_hard_filters_passes_clean_job() -> None:
    """Jobs without blocklist matches should pass."""
    filters = HardFilters(title_blocklist=["sales"], company_blocklist=["agency"])

    assert apply_hard_filters(make_job(), filters) is None
