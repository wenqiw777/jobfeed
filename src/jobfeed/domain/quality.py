"""Job description quality assessment helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobfeed.domain.models import QualityBand

STUB_MAX_CHARS = 199
PARTIAL_MAX_CHARS = 500
GOOD_MAX_CHARS = 1000

# Cross-run enrichment idempotency policy (carried over from the legacy source).
# A JD enriched within this window with acceptable quality should not be
# re-fetched: the retry yields the same JD and only burns the source's
# navigation / anti-bot budget.
ENRICH_FRESHNESS_TTL = timedelta(days=7)
# A PARTIAL JD this long is treated as fresh too: its body is essentially
# complete (it's only PARTIAL because section headers didn't match), so a retry
# returns the same verdict.
LONG_PARTIAL_FRESHNESS_CHARS = 1500


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


_GOOD_RANK = _QUALITY_RANK[QualityBand.GOOD.value]


def is_jd_fresh(
    *,
    quality: QualityBand | None,
    jd_text: str | None,
    enriched_at: datetime | None,
    now: datetime,
    ttl: timedelta = ENRICH_FRESHNESS_TTL,
) -> bool:
    """Return whether a stored JD is fresh enough to skip re-enrichment.

    Fresh means it was enriched within ``ttl`` (default 7 days) AND has non-empty
    JD text whose quality is GOOD/FULL — or PARTIAL but already
    ``LONG_PARTIAL_FRESHNESS_CHARS`` long. A worse/older JD is not fresh, so the
    source re-fetches it.

    Args:
        quality: Stored JD quality band.
        jd_text: Stored JD text.
        enriched_at: When the JD was last enriched (UTC-aware or naive UTC).
        now: Current UTC time for the TTL comparison.
        ttl: Freshness window.

    Returns:
        True when re-enrichment would be wasted work.
    """
    if enriched_at is None or not jd_text:
        return False
    if quality_rank(quality) < _GOOD_RANK and not (
        quality == QualityBand.PARTIAL and len(jd_text) >= LONG_PARTIAL_FRESHNESS_CHARS
    ):
        return False
    enriched = enriched_at if enriched_at.tzinfo else enriched_at.replace(tzinfo=UTC)
    return (now - enriched) < ttl


__all__ = [
    "ENRICH_FRESHNESS_TTL",
    "LONG_PARTIAL_FRESHNESS_CHARS",
    "assess_quality",
    "is_jd_fresh",
    "quality_rank",
]
