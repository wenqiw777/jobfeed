"""Pure sort keys for the jobs view list.

Defines the fixed triage verdict-group order and the Library sort lookup.
Split out of ``services/jobs_view.py`` to keep both modules under the
300-line gate; no IO, no service state.
"""

from __future__ import annotations

from collections.abc import Callable

# Sort vocabulary is canonical in the domain (JobsViewQuery validates it);
# re-exported here so service-layer callers keep one import site.
from jobfeed.domain.models_views import DEFAULT_SORT, VALID_SORTS, JobsViewRow

# Verdict-group ranks: apply -> consider -> skip -> derived below-threshold
# ("below threshold" is NOT a Verdict value; it is derived from
# stage_b_status = 'skipped_below_threshold') -> unscored.
_VERDICT_GROUP_RANK = {"apply": 0, "consider": 1, "skip": 2}
_BELOW_THRESHOLD_GROUP = 3
_UNSCORED_GROUP = 4
_BELOW_THRESHOLD_STATUS = "skipped_below_threshold"


def verdict_group_sort_key(row: JobsViewRow) -> tuple[int, int, int, float, int]:
    """Build the fixed triage sort key for one row.

    Order: verdict group (apply, consider, skip, derived below-threshold,
    unscored), then score descending inside the group (Stage B fit score
    with Stage A fallback, unscored last), then the deterministic tiebreak.

    Args:
        row: View row to key.

    Returns:
        Sort key tuple (smaller sorts earlier).
    """
    return (_verdict_group(row), *_score_rank(row), *_tiebreak(row))


def _verdict_group(row: JobsViewRow) -> int:
    """Rank a row into its verdict group (lower = earlier on screen)."""
    if row.verdict is not None:
        return _VERDICT_GROUP_RANK.get(row.verdict, _UNSCORED_GROUP)
    if row.stage_b_status == _BELOW_THRESHOLD_STATUS:
        return _BELOW_THRESHOLD_GROUP
    return _UNSCORED_GROUP


def _score_rank(row: JobsViewRow) -> tuple[int, int]:
    """Stage B fit score desc with Stage A fallback; unscored rows last."""
    score = row.stage_b_fit_score
    if score is None:
        score = row.stage_a_score
    if score is None:
        return (1, 0)
    return (0, -score)


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
    """Library order: most recently posted first, missing posted_at last."""
    posted = row.job.posted_at
    if posted is None:
        return (1, 0.0, *_tiebreak(row))
    return (0, -posted.timestamp(), *_tiebreak(row))


def _key_score_desc(row: JobsViewRow) -> tuple[int, int, float, int]:
    """Library order: best score first (fit score, Stage A fallback)."""
    return (*_score_rank(row), *_tiebreak(row))


def _key_company_asc(row: JobsViewRow) -> tuple[str, float, int]:
    """Library order: company name ascending, case-insensitive."""
    return (row.job.company.casefold(), *_tiebreak(row))


#: Library sort-key lookup, one entry per ``VALID_SORTS`` value.
LIBRARY_SORT_KEYS: dict[str, Callable[[JobsViewRow], tuple[float | int | str, ...]]]
LIBRARY_SORT_KEYS = {
    "discovered_desc": _key_discovered_desc,
    "posted_desc": _key_posted_desc,
    "score_desc": _key_score_desc,
    "company_asc": _key_company_asc,
}


__all__ = [
    "DEFAULT_SORT",
    "LIBRARY_SORT_KEYS",
    "VALID_SORTS",
    "verdict_group_sort_key",
]
