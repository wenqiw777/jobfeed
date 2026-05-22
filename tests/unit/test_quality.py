"""Unit tests for deterministic JD quality assessment."""

from __future__ import annotations

from jobfeed.domain.models import QualityBand
from jobfeed.domain.quality import (
    GOOD_MAX_CHARS,
    PARTIAL_MAX_CHARS,
    STUB_MAX_CHARS,
    assess_quality,
)

FULL_TEXT_LENGTH = 1500
GOOD_TEXT_LENGTH = 700
PARTIAL_TEXT_LENGTH = 300
STUB_TEXT_LENGTH = 100


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
