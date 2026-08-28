"""SQLite pending and hydrated evaluation read queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._sqlite_evaluation_rows import _evaluation_from_row
from jobfeed.adapters.store._sqlite_values import _job_from_row, _utc_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import JobEvaluation, JobPosting
from jobfeed.domain.scoring import MAX_STAGE_RETRIES

_EVALUATION_COLUMNS = """evaluations.stage_a_score, evaluations.stage_a_one_line,
    evaluations.stage_a_timing_eligible, evaluations.stage_a_status,
    evaluations.stage_a_error, evaluations.stage_a_model,
    evaluations.stage_a_cost_usd, evaluations.stage_a_prompt_hash,
    evaluations.stage_a_resume_hash, evaluations.stage_b_verdict,
    evaluations.stage_b_jd_summary, evaluations.stage_b_verdict_json,
    evaluations.stage_b_summary_json, evaluations.stage_b_fit_json,
    evaluations.stage_b_hooks_json, evaluations.stage_b_status,
    evaluations.stage_b_error, evaluations.stage_b_model,
    evaluations.stage_b_cost_usd, evaluations.stage_b_prompt_hash,
    evaluations.stage_b_resume_hash"""


async def _load_pending_stage_a(
    lifecycle: SqliteLifecycle,
    *,
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
) -> list[JobPosting]:
    """Load the non-claiming Stage A corpus with stable ordering."""
    _validate_limit(limit)
    conditions = [
        _corpus_condition(corpus),
        "jobs.closed_at IS NULL",
        "COALESCE(jobs.hard_filter,'')=''",
    ]
    conditions.append(
        "(evaluations.stage_a_status IS NULL OR evaluations.stage_a_status<>'error' "
        f"OR evaluations.stage_a_error_count<{MAX_STAGE_RETRIES})"
    )
    params: list[object] = []
    if quality_bands:
        placeholders = ",".join("?" for _ in quality_bands)
        conditions.append(f"jobs.jd_quality IN ({placeholders})")
        params.extend(sorted(quality_bands))
    if max_days is not None:
        conditions.append("jobs.discovered_at>=?")
        params.append(_utc_text(datetime.now(UTC) - timedelta(days=max_days)))
    params.append(limit)
    return await _job_query(
        lifecycle,
        "SELECT jobs.* FROM jobs LEFT JOIN evaluations "
        "ON evaluations.job_id=jobs.id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY jobs.discovered_at DESC, jobs.id DESC LIMIT ?",
        params,
    )


async def _load_pending_stage_b(
    lifecycle: SqliteLifecycle,
    *,
    limit: int,
    max_days: int | None,
    stage_a_threshold: int | None,
) -> list[JobPosting]:
    """Load non-claiming Stage B null/error rows under the retry cap."""
    _validate_limit(limit)
    conditions = [
        "evaluations.stage_a_status='completed'",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status='error')",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status<>'error' "
        f"OR evaluations.stage_b_error_count<{MAX_STAGE_RETRIES})",
    ]
    params: list[object] = []
    if max_days is not None:
        conditions.append("jobs.discovered_at>=?")
        params.append(_utc_text(datetime.now(UTC) - timedelta(days=max_days)))
    if stage_a_threshold is not None:
        conditions.append("evaluations.stage_a_score>=?")
        params.append(stage_a_threshold)
    params.append(limit)
    return await _job_query(
        lifecycle,
        "SELECT jobs.* FROM jobs JOIN evaluations ON evaluations.job_id=jobs.id "
        "WHERE "
        + " AND ".join(conditions)
        + " ORDER BY jobs.discovered_at DESC, jobs.id DESC LIMIT ?",
        params,
    )


async def _list_evaluated_jobs(
    lifecycle: SqliteLifecycle,
    limit: int,
) -> list[JobEvaluation]:
    """List inner-joined evaluations by job recency and ID."""
    _validate_limit(limit)
    return await _evaluation_query(
        lifecycle,
        f"SELECT jobs.*, {_EVALUATION_COLUMNS} FROM jobs JOIN evaluations "
        "ON evaluations.job_id=jobs.id "
        "ORDER BY jobs.discovered_at DESC, jobs.id DESC LIMIT ?",
        (limit,),
    )


async def _get_evaluation(
    lifecycle: SqliteLifecycle,
    job_id: str,
) -> JobEvaluation | None:
    """Load a left-joined evaluation, including existing unevaluated jobs."""
    rows = await _evaluation_query(
        lifecycle,
        f"SELECT jobs.*, {_EVALUATION_COLUMNS} FROM jobs LEFT JOIN evaluations "
        "ON evaluations.job_id=jobs.id WHERE jobs.id=?",
        (int(job_id),),
    )
    return rows[0] if rows else None


async def _top_evaluated_jobs(
    lifecycle: SqliteLifecycle,
    *,
    min_score: int,
    limit: int,
) -> list[JobEvaluation]:
    """List completed Stage B evaluations by score, recency, and identity."""
    _validate_limit(limit)
    return await _evaluation_query(
        lifecycle,
        f"SELECT jobs.*, {_EVALUATION_COLUMNS} FROM jobs JOIN evaluations "
        "ON evaluations.job_id=jobs.id WHERE evaluations.stage_b_status='completed' "
        "AND evaluations.stage_a_score>=? ORDER BY evaluations.stage_a_score DESC, "
        "jobs.discovered_at DESC, jobs.id DESC LIMIT ?",
        (min_score, limit),
    )


async def _job_query(
    lifecycle: SqliteLifecycle,
    statement: str,
    params: list[object],
) -> list[JobPosting]:
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(statement, params)
        rows = await cursor.fetchall()
        await cursor.close()
    return [_job_from_row(row) for row in rows]


async def _evaluation_query(
    lifecycle: SqliteLifecycle,
    statement: str,
    params: tuple[object, ...],
) -> list[JobEvaluation]:
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(statement, params)
        rows = await cursor.fetchall()
        await cursor.close()
    return [_evaluation_from_row(row) for row in rows]


def _corpus_condition(corpus: str) -> str:
    if corpus == "unrated":
        return (
            "(evaluations.job_id IS NULL OR evaluations.stage_a_status IS NULL "
            "OR evaluations.stage_a_status='error')"
        )
    if corpus == "failed":
        return "evaluations.stage_a_status='error'"
    if corpus == "all":
        return "TRUE"
    raise ValueError(f"unknown corpus: {corpus!r}")


def _validate_limit(limit: int) -> None:
    if limit < 0:
        raise ValueError("limit must be nonnegative")
