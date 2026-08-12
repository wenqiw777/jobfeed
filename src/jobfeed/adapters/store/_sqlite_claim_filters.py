"""Build frozen SQLite evaluation eligibility predicates and ordered queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from jobfeed.adapters.store._sqlite_capability_support import (
    _placeholders,
    _require_utc_timestamp,
    _validate_limit,
)
from jobfeed.domain.scoring import MAX_STAGE_RETRIES

_CLAIM_TTL: Final = timedelta(hours=1)
_CORPORA: Final = frozenset({"unrated", "failed", "all"})


@dataclass(frozen=True, kw_only=True)
class StageAQuery:
    """Validated Stage A candidate query inputs."""

    now: datetime
    limit: int = 100
    quality_bands: frozenset[str] | None = None
    corpus: str = "unrated"
    max_days: int | None = None
    job_ids: tuple[int, ...] = ()
    exclude_gate_failed: bool = False
    after: tuple[datetime, int] | None = None
    is_gate_query: bool = False


@dataclass(frozen=True, kw_only=True)
class StageBQuery:
    """Validated Stage B candidate query inputs."""

    now: datetime
    limit: int = 100
    max_days: int | None = None
    stage_a_threshold: int | None = None


def _build_stage_a_select(query: StageAQuery) -> tuple[str, list[object]]:
    """Build the shared Stage A preview, gate, and claim candidate SELECT."""
    _validate_stage_a(query)
    params: list[object] = []
    conditions = [
        "j.closed_at IS NULL",
        "(e.stage_a_status IS NOT 'error' "
        f"OR e.stage_a_error_count < {MAX_STAGE_RETRIES})",
    ]
    cutoff = _require_utc_timestamp(query.now - _CLAIM_TTL, "now")
    conditions.append(_stage_a_status(query.corpus, cutoff, params))
    _append_common_filters(query, conditions, params)
    if query.job_ids:
        conditions.append(f"j.id IN ({_placeholders(query.job_ids)})")
        params.extend(query.job_ids)
    if query.is_gate_query:
        _append_gate_filters(query, conditions, params)
    params.append(query.limit)
    return (
        "SELECT j.* FROM jobs j LEFT JOIN evaluations e ON e.job_id=j.id "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY j.discovered_at DESC, j.id DESC LIMIT ?",
        params,
    )


def _build_stage_b_select(query: StageBQuery) -> tuple[str, list[object]]:
    """Build the Stage B claim candidate SELECT with strict stale cutoff."""
    _validate_limit(query.limit)
    _require_utc_timestamp(query.now)
    params: list[object] = []
    conditions = [
        "e.stage_a_status='completed'",
        "(e.stage_b_status IS NOT 'error' "
        f"OR e.stage_b_error_count < {MAX_STAGE_RETRIES})",
    ]
    if query.max_days is not None:
        conditions.append("j.discovered_at>=?")
        params.append(
            _require_utc_timestamp(query.now - timedelta(days=query.max_days))
        )
    if query.stage_a_threshold is not None:
        conditions.append("e.stage_a_score>=?")
        params.append(query.stage_a_threshold)
    conditions.append(
        "(e.stage_b_status IS NULL OR e.stage_b_status='error' OR "
        "(e.stage_b_status='in_progress' AND e.updated_at<? "
        "AND e.stage_b_verdict IS NULL))"
    )
    params.extend([_require_utc_timestamp(query.now - _CLAIM_TTL), query.limit])
    return (
        "SELECT j.* FROM jobs j JOIN evaluations e ON e.job_id=j.id "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY j.discovered_at DESC, j.id DESC LIMIT ?",
        params,
    )


def _validate_stage_a(query: StageAQuery) -> None:
    _validate_limit(query.limit)
    _require_utc_timestamp(query.now)
    if query.corpus not in _CORPORA:
        raise ValueError(f"unknown corpus: {query.corpus!r}")


def _stage_a_status(
    corpus: str,
    cutoff: str,
    params: list[object],
) -> str:
    params.append(cutoff)
    if corpus == "all":
        return "(e.stage_a_status IS NOT 'in_progress' OR e.updated_at<?)"
    if corpus == "failed":
        return (
            "(e.stage_a_status='error' OR (e.stage_a_status='in_progress' "
            "AND e.updated_at<? AND e.stage_a_error IS NOT NULL))"
        )
    return (
        "(e.job_id IS NULL OR e.stage_a_status IS NULL OR "
        "e.stage_a_status='error' OR (e.stage_a_status='in_progress' "
        "AND e.updated_at<? AND "
        "(e.stage_a_score IS NULL OR e.stage_a_error IS NOT NULL)))"
    )


def _append_common_filters(
    query: StageAQuery,
    conditions: list[str],
    params: list[object],
) -> None:
    if query.quality_bands:
        bands = sorted(query.quality_bands)
        conditions.append(f"j.jd_quality IN ({_placeholders(bands)})")
        params.extend(bands)
    if query.max_days is not None:
        conditions.append("j.discovered_at>=?")
        params.append(
            _require_utc_timestamp(query.now - timedelta(days=query.max_days))
        )


def _append_gate_filters(
    query: StageAQuery,
    conditions: list[str],
    params: list[object],
) -> None:
    if query.after is not None:
        after_time, after_id = query.after
        timestamp = _require_utc_timestamp(after_time, "after timestamp")
        conditions.append("(j.discovered_at<? OR (j.discovered_at=? AND j.id<?))")
        params.extend([timestamp, timestamp, after_id])
    if query.corpus == "unrated":
        conditions.extend(
            [
                "e.stage_a_status IS NOT 'completed'",
                "(COALESCE(j.company_norm,'')='' OR COALESCE(j.title_norm,'')='' "
                "OR NOT EXISTS (SELECT 1 FROM jobs twin "
                "JOIN evaluations te ON te.job_id=twin.id "
                "WHERE twin.company_norm=j.company_norm "
                "AND twin.title_norm=j.title_norm "
                "AND te.stage_a_status='completed'))",
            ]
        )
    if query.exclude_gate_failed:
        conditions.append("j.ml_gate_result IS NOT 'fail'")
