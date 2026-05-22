"""Job description quality assessment helpers."""

from __future__ import annotations

from jobfeed.domain.models import QualityBand

STUB_MAX_CHARS = 199
PARTIAL_MAX_CHARS = 500
GOOD_MAX_CHARS = 1000


def assess_quality(jd_text: str | None) -> QualityBand:
    """Classify JD text quality by deterministic Phase 0 length bands.

    Args:
        jd_text: Job description text when available.

    Returns:
        Quality band used by source and evaluation services.
    """
    if jd_text is None:
        return QualityBand.MISSING
    normalized_text = jd_text.strip()
    if not normalized_text:
        return QualityBand.MISSING
    length = len(normalized_text)
    if length <= STUB_MAX_CHARS:
        return QualityBand.STUB
    if length <= PARTIAL_MAX_CHARS:
        return QualityBand.PARTIAL
    if length <= GOOD_MAX_CHARS:
        return QualityBand.GOOD
    return QualityBand.FULL


__all__ = ["assess_quality"]
