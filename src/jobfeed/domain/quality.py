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


# Higher rank = better JD. Used by save_job's quality ladder so a worse incoming
# scrape never overwrites a better stored one. Unknown/None ranks lowest.
_QUALITY_RANK: dict[str, int] = {
    QualityBand.FULL.value: 5,
    QualityBand.GOOD.value: 4,
    QualityBand.PARTIAL.value: 3,
    QualityBand.STUB.value: 2,
    QualityBand.MISSING.value: 1,
    QualityBand.ABANDONED.value: 0,
}


def quality_rank(quality: QualityBand | str | None) -> int:
    """Return an orderable rank for a JD quality band (higher = better).

    Args:
        quality: A QualityBand, its string value, or None.

    Returns:
        Integer rank; -1 for None or an unrecognized value.
    """
    if quality is None:
        return -1
    return _QUALITY_RANK.get(str(quality), -1)


__all__ = ["assess_quality", "quality_rank"]
