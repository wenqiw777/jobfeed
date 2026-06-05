"""Unit tests for deterministic JD quality assessment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobfeed.domain.models import QualityBand
from jobfeed.domain.quality import (
    ENRICH_FRESHNESS_TTL,
    GOOD_MAX_CHARS,
    LONG_PARTIAL_FRESHNESS_CHARS,
    PARTIAL_MAX_CHARS,
    STUB_MAX_CHARS,
    assess_quality,
    is_jd_fresh,
    quality_rank,
)

FULL_TEXT_LENGTH = 1500
GOOD_TEXT_LENGTH = 700
PARTIAL_TEXT_LENGTH = 300
STUB_TEXT_LENGTH = 100

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def test_assess_quality_maps_missing_and_full_text() -> None:
    """Quality assessment should map empty and long JD text deterministically."""
    assert assess_quality(None) is QualityBand.MISSING
    assert assess_quality("") is QualityBand.MISSING
    assert assess_quality("   ") is QualityBand.MISSING
    assert assess_quality("x" * FULL_TEXT_LENGTH) is QualityBand.FULL


def test_assess_quality_maps_intermediate_bands() -> None:
    """Quality assessment should preserve the Phase 0 length boundaries."""
    assert assess_quality("x" * STUB_TEXT_LENGTH) is QualityBand.STUB
    assert assess_quality("x" * PARTIAL_TEXT_LENGTH) is QualityBand.PARTIAL
    assert assess_quality("x" * GOOD_TEXT_LENGTH) is QualityBand.GOOD


def test_assess_quality_uses_stripped_length() -> None:
    """Whitespace should not inflate the JD quality band."""
    assert assess_quality((" " * PARTIAL_TEXT_LENGTH) + ("x" * 50)) is QualityBand.STUB


def test_assess_quality_preserves_boundary_values() -> None:
    """Boundary values should make the inclusive thresholds explicit."""
    assert assess_quality("x" * (STUB_MAX_CHARS - 1)) is QualityBand.STUB
    assert assess_quality("x" * STUB_MAX_CHARS) is QualityBand.STUB
    assert assess_quality("x" * (STUB_MAX_CHARS + 1)) is QualityBand.PARTIAL
    assert assess_quality("x" * PARTIAL_MAX_CHARS) is QualityBand.PARTIAL
    assert assess_quality("x" * (PARTIAL_MAX_CHARS + 1)) is QualityBand.GOOD
    assert assess_quality("x" * GOOD_MAX_CHARS) is QualityBand.GOOD
    assert assess_quality("x" * (GOOD_MAX_CHARS + 1)) is QualityBand.FULL


def test_quality_rank_orders_bands_high_to_low() -> None:
    """quality_rank ranks FULL highest down to ABANDONED, with None lowest."""
    assert quality_rank(QualityBand.FULL) > quality_rank(QualityBand.GOOD)
    assert quality_rank(QualityBand.GOOD) > quality_rank(QualityBand.PARTIAL)
    assert quality_rank(QualityBand.PARTIAL) > quality_rank(QualityBand.STUB)
    assert quality_rank(QualityBand.STUB) > quality_rank(QualityBand.MISSING)
    assert quality_rank(QualityBand.MISSING) > quality_rank(QualityBand.ABANDONED)
    assert quality_rank(None) < quality_rank(QualityBand.ABANDONED)


def test_quality_rank_accepts_strings_and_unknowns() -> None:
    """quality_rank accepts band strings; unknown/None rank below all bands."""
    assert quality_rank("full") == quality_rank(QualityBand.FULL)
    assert quality_rank("bogus") == quality_rank(None)


def test_is_jd_fresh_true_for_recent_good_or_full() -> None:
    """A recent GOOD/FULL JD is fresh (re-enrich would be wasted)."""
    recent = NOW - timedelta(days=1)
    assert is_jd_fresh(
        quality=QualityBand.GOOD, jd_text="x" * 700, enriched_at=recent, now=NOW
    )
    assert is_jd_fresh(
        quality=QualityBand.FULL, jd_text="x" * 2000, enriched_at=recent, now=NOW
    )


def test_is_jd_fresh_long_partial_escape_hatch() -> None:
    """A short PARTIAL is not fresh; a long PARTIAL (>=1500) is."""
    recent = NOW - timedelta(days=1)
    assert not is_jd_fresh(
        quality=QualityBand.PARTIAL, jd_text="x" * 400, enriched_at=recent, now=NOW
    )
    assert is_jd_fresh(
        quality=QualityBand.PARTIAL,
        jd_text="x" * LONG_PARTIAL_FRESHNESS_CHARS,
        enriched_at=recent,
        now=NOW,
    )


def test_is_jd_fresh_false_when_stale_missing_or_empty() -> None:
    """TTL expiry, missing enriched_at, or empty JD all make it not fresh."""
    stale = NOW - ENRICH_FRESHNESS_TTL - timedelta(seconds=1)
    recent = NOW - timedelta(days=1)
    assert not is_jd_fresh(
        quality=QualityBand.GOOD, jd_text="x" * 700, enriched_at=stale, now=NOW
    )
    assert not is_jd_fresh(
        quality=QualityBand.GOOD, jd_text="x" * 700, enriched_at=None, now=NOW
    )
    assert not is_jd_fresh(
        quality=QualityBand.GOOD, jd_text="", enriched_at=recent, now=NOW
    )


def test_is_jd_fresh_treats_naive_enriched_at_as_utc() -> None:
    """A naive enriched_at is interpreted as UTC, not crashed on."""
    naive_recent = (NOW - timedelta(days=1)).replace(tzinfo=None)
    assert is_jd_fresh(
        quality=QualityBand.GOOD, jd_text="x" * 700, enriched_at=naive_recent, now=NOW
    )
