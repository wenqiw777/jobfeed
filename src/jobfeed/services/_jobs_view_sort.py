"""Pure sort keys for the jobs view list.

Defines the fixed triage verdict-group order and the Library sort lookup.
Split out of ``services/jobs_view.py`` to keep both modules under the
300-line gate; no IO, no service state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from math import exp2

# Sort vocabulary is canonical in the domain (JobsViewQuery validates it);
# re-exported here so service-layer callers keep one import site.
from jobfeed.domain.models_views import DEFAULT_SORT, VALID_SORTS, JobsViewRow

# Unified match tiers are hard ranking boundaries. Legacy Stage A/B verdicts
# are deliberately absent: only canonical evaluation results can rank.
_VERDICT_GROUP_RANK = {
    "strong_match": 0,
    "possible_match": 1,
    "weak_match": 2,
    "ineligible": 3,
}
_UNSCORED_GROUP = 4

_FIT_WEIGHT = 0.85
_FRESHNESS_WEIGHT = 0.15
_FRESHNESS_HALF_LIFE_DAYS = 7.0


def verdict_group_sort_key(row: JobsViewRow) -> tuple[int, int, float, float, int]:
    """Build the fixed triage sort key for one row.

    Order: unified match tier, then 85% canonical match score plus 15%
    freshness inside the tier, then the deterministic tiebreak.

    Args:
        row: View row to key.

    Returns:
        Sort key tuple (smaller sorts earlier).
    """
    return (_verdict_group(row), *_triage_rank(row), *_tiebreak(row))


def _verdict_group(row: JobsViewRow) -> int:
    """Rank a row into its verdict group (lower = earlier on screen)."""
    return _VERDICT_GROUP_RANK.get(row.evaluation_verdict or "", _UNSCORED_GROUP)


def _score_rank(row: JobsViewRow) -> tuple[int, int]:
    """Canonical unified score descending; unevaluated rows last."""
    score = row.evaluation_score
    if score is None:
        return (1, 0)
    return (0, -score)


def _triage_rank(row: JobsViewRow) -> tuple[int, float]:
    """Rank one unified tier by canonical score and posting freshness."""
    score = row.evaluation_score
    if score is None:
        return (1, 0.0)
    ranking_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rank_signal = _FIT_WEIGHT * score + _FRESHNESS_WEIGHT * _freshness_score(
        row, now=ranking_day
    )
    return (0, -rank_signal)


def _freshness_score(row: JobsViewRow, *, now: datetime) -> float:
    """Return 0..100 freshness with a seven-day exponential half-life."""
    posted = row.job.posted_at or row.job.discovered_at
    age_days = max(0.0, (now - posted).total_seconds() / 86_400.0)
    return 100.0 * exp2(-age_days / _FRESHNESS_HALF_LIFE_DAYS)


def _tiebreak(row: JobsViewRow) -> tuple[float, int]:
    """Deterministic final tiebreak: discovered_at desc, then job id desc.

    Mirrors the store's ``discovered_at DESC, id DESC`` order. Store ids are
    numeric strings; a non-numeric or missing id sorts as 0.
    """
    raw_id = row.job.id or "0"
    numeric_id = int(raw_id) if raw_id.isdigit() else 0
    return (-row.job.discovered_at.timestamp(), -numeric_id)


def _key_discovered_desc(row: JobsViewRow) -> tuple[float, int]:
    """Library default order: newest discovered first."""
    return _tiebreak(row)


def _key_posted_desc(row: JobsViewRow) -> tuple[int, float, float, int]:
    """Library order: newest posting, estimating missing dates from discovery."""
    posted = row.job.posted_at or row.job.discovered_at
    return (0, -posted.timestamp(), *_tiebreak(row))


def _key_posted_asc(row: JobsViewRow) -> tuple[int, float, float, int]:
    """Library order: oldest posting, estimating missing dates from discovery."""
    posted = row.job.posted_at or row.job.discovered_at
    return (0, posted.timestamp(), *_tiebreak(row))


def _key_score_desc(row: JobsViewRow) -> tuple[int, int, float, int]:
    """Library order: best canonical unified score first."""
    return (*_score_rank(row), *_tiebreak(row))


def _key_score_asc(row: JobsViewRow) -> tuple[int, int, float, int]:
    """Lowest canonical unified score first, unevaluated rows last."""
    score = row.evaluation_score
    if score is None:
        return (1, 0, *_tiebreak(row))
    return (0, score, *_tiebreak(row))


def _key_company_asc(row: JobsViewRow) -> tuple[str, float, int]:
    """Library order: company name ascending, case-insensitive."""
    return (row.job.company.casefold(), *_tiebreak(row))


#: Library sort-key lookup, one entry per ``VALID_SORTS`` value.
LIBRARY_SORT_KEYS: dict[str, Callable[[JobsViewRow], tuple[float | int | str, ...]]]
LIBRARY_SORT_KEYS = {
    "discovered_desc": _key_discovered_desc,
    "posted_asc": _key_posted_asc,
    "posted_desc": _key_posted_desc,
    "score_asc": _key_score_asc,
    "score_desc": _key_score_desc,
    "company_asc": _key_company_asc,
}


__all__ = [
    "DEFAULT_SORT",
    "LIBRARY_SORT_KEYS",
    "VALID_SORTS",
    "verdict_group_sort_key",
]
