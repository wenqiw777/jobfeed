"""Unit tests for the store normalization helpers."""

from __future__ import annotations

from jobfeed.adapters.store._normalize import (
    normalize,
    normalize_company,
    quality_rank,
)


def test_normalize_lowercases_and_collapses_non_alnum() -> None:
    """normalize should lowercase and collapse non-alnum to spaces."""
    assert normalize("New York, NY") == "new york ny"
    assert normalize("San Francisco") == "san francisco"
    assert normalize("") == ""
    assert normalize(None) == ""


def test_normalize_company_strips_corporate_suffixes() -> None:
    """normalize_company should iteratively peel trailing suffixes."""
    assert normalize_company("Palantir Technologies, Inc.") == "palantir"
    assert normalize_company("Acme Corp") == "acme"
    assert normalize_company("Meta") == "meta"
    assert normalize_company("Amazon.com, Inc.") == "amazon com"
    assert normalize_company(None) == ""
    assert normalize_company("") == ""


def test_normalize_company_keeps_last_token() -> None:
    """Single-token company names should not be stripped even if in suffix list."""
    assert normalize_company("Inc") == "inc"
    assert normalize_company("Technologies") == "technologies"


def test_quality_rank_ordering() -> None:
    """Quality ladder should preserve documented ordering."""
    assert quality_rank("full") > quality_rank("good")
    assert quality_rank("good") > quality_rank("partial")
    assert quality_rank("partial") > quality_rank("stub")
    assert quality_rank("stub") > quality_rank("missing")
    assert quality_rank("missing") == quality_rank("abandoned")
    assert quality_rank(None) < quality_rank("missing")


def test_quality_rank_unknown_returns_negative() -> None:
    """Unknown quality values should rank below None."""
    assert quality_rank("bogus") == quality_rank(None)
