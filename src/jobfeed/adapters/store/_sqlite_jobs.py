"""SQLite jobs persistence with quality-aware natural-key upserts."""

from __future__ import annotations

from typing import Any

import aiosqlite

from jobfeed.adapters.store._normalize import normalize, normalize_company
from jobfeed.adapters.store._sqlite_values import (
    _canonical_json,
    _job_from_row,
    _utc_now_text,
    _utc_text,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.ml_features import classify_role_type
from jobfeed.domain.models import JobPosting, MLGateResult, SaveJobResult
from jobfeed.domain.quality import quality_rank

_INSERT_JOB_SQL = """INSERT INTO jobs (
    platform, canonical_id, url, title, company, location,
    jd_text, jd_quality, posted_at, discovered_at, enriched_at, enrich_source,
    company_norm, title_norm, location_norm, closed_at, enrich_error, role_type
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id"""


async def _save_job(lifecycle: SqliteLifecycle, job: JobPosting) -> SaveJobResult:
    """Atomically insert or quality-aware update one natural-key job."""
    values = _job_values(job)
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute("BEGIN IMMEDIATE")
        try:
            existing = await _job_by_natural_key(connection, job)
            if existing is None:
                cursor = await connection.execute(_INSERT_JOB_SQL, values)
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    raise RuntimeError("SQLite job insert returned no identity")
                result = SaveJobResult(
                    job_id=str(row["id"]), inserted=True, updated=False
                )
            else:
                await _update_job(connection, job, existing)
                result = SaveJobResult(
                    job_id=str(existing["id"]), inserted=False, updated=True
                )
            await connection.commit()
            return result
        except BaseException:
            await connection.rollback()
            raise


async def _get_job(lifecycle: SqliteLifecycle, job_id: str) -> JobPosting | None:
    """Load one job by its decimal SQLite identity."""
    numeric_id = int(job_id)
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            "SELECT * FROM jobs WHERE id=?", (numeric_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
    return _job_from_row(row) if row is not None else None


async def _list_jobs(lifecycle: SqliteLifecycle, limit: int) -> list[JobPosting]:
    """List jobs by descending discovery time and identity."""
    _validate_limit(limit)
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            "SELECT * FROM jobs ORDER BY discovered_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [_job_from_row(row) for row in rows]


async def _job_exists(
    lifecycle: SqliteLifecycle,
    *,
    platform: str,
    canonical_id: str,
) -> bool:
    """Return whether an exact, case-sensitive natural key exists."""
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT 1 FROM jobs WHERE platform=? AND canonical_id=?",
            (platform, canonical_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return row is not None


async def _save_ml_gate_result(
    lifecycle: SqliteLifecycle,
    job_id: str,
    result: MLGateResult,
) -> None:
    """Persist the latest ML-gate decision and canonical feature JSON."""
    numeric_id = int(job_id)
    tags = _canonical_json(result.domain_tags) if result.domain_tags else None
    tech = _canonical_json(result.tech_required) if result.tech_required else None
    async with lifecycle.connection() as connection:
        await connection.execute(
            """UPDATE jobs SET
                ml_gate_score=?, ml_gate_result=?, ml_gate_fail_reason=?, ml_gate_at=?,
                ml_gate_version=?, is_swe_role=?, seniority_level=?, degree_required=?,
                clearance_required=?, school_restricted=?, yoe_min=?, domain_tags=?,
                tech_required=?, role_type=? WHERE id=?""",
            (
                result.score,
                result.result,
                result.fail_reason,
                _utc_now_text(),
                result.version,
                _bool_int(result.is_swe_role),
                result.seniority_level,
                result.degree_required,
                _bool_int(result.clearance_required),
                _bool_int(result.school_restricted),
                result.yoe_min,
                tags,
                tech,
                result.role_type,
                numeric_id,
            ),
        )


def _job_values(job: JobPosting) -> tuple[object, ...]:
    return (
        job.platform,
        job.canonical_id,
        job.url,
        job.title,
        job.company,
        job.location,
        job.jd_text,
        job.jd_quality.value if job.jd_quality else None,
        _utc_text(job.posted_at) if job.posted_at else None,
        _utc_text(job.discovered_at),
        _utc_text(job.enriched_at) if job.enriched_at else None,
        job.enrich_source,
        normalize_company(job.company),
        normalize(job.title),
        normalize(job.location),
        _utc_text(job.closed_at) if job.closed_at else None,
        job.enrich_error,
        classify_role_type(job.title, job.jd_text or ""),
    )


async def _job_by_natural_key(
    connection: aiosqlite.Connection,
    job: JobPosting,
) -> aiosqlite.Row | None:
    cursor = await connection.execute(
        "SELECT * FROM jobs WHERE platform=? AND canonical_id=?",
        (job.platform, job.canonical_id),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def _update_job(
    connection: aiosqlite.Connection,
    job: JobPosting,
    existing: aiosqlite.Row,
) -> None:
    incoming_wins = quality_rank(job.jd_quality) >= quality_rank(existing["jd_quality"])
    jd_text = (
        (job.jd_text if job.jd_text is not None else existing["jd_text"])
        if incoming_wins
        else existing["jd_text"]
    )
    jd_quality = (
        (job.jd_quality.value if job.jd_quality else existing["jd_quality"])
        if incoming_wins
        else existing["jd_quality"]
    )
    enrich_source = (
        (job.enrich_source or existing["enrich_source"])
        if incoming_wins
        else existing["enrich_source"]
    )
    gate_changed = job.title != existing["title"] or jd_text != existing["jd_text"]
    role_type = classify_role_type(job.title, jd_text or "")
    closed_at = (
        None
        if job.jd_text is not None
        else existing["closed_at"] or _time(job.closed_at)
    )
    enrich_error = (
        None
        if job.jd_text is not None
        else (
            job.enrich_error
            if job.enrich_error is not None
            else existing["enrich_error"]
        )
    )
    gate_sql = (
        ", ml_gate_score=NULL, ml_gate_result=NULL, ml_gate_fail_reason=NULL, "
        "ml_gate_at=NULL, ml_gate_version=NULL"
        if gate_changed
        else ""
    )
    await connection.execute(
        """UPDATE jobs SET url=?, title=?, company=?, location=?, jd_text=?,
            jd_quality=?, posted_at=?, discovered_at=?, enriched_at=?, enrich_source=?,
            company_norm=?, title_norm=?, location_norm=?, closed_at=?,
            enrich_error=?, role_type=?"""
        + gate_sql
        + " WHERE id=?",
        (
            job.url,
            job.title,
            job.company,
            job.location,
            jd_text,
            jd_quality,
            _time(job.posted_at) or existing["posted_at"],
            _utc_text(job.discovered_at),
            _time(job.enriched_at) or existing["enriched_at"],
            enrich_source,
            normalize_company(job.company),
            normalize(job.title),
            normalize(job.location),
            closed_at,
            enrich_error,
            role_type,
            existing["id"],
        ),
    )


def _time(value: Any) -> str | None:
    return _utc_text(value) if value is not None else None


def _bool_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _validate_limit(limit: int) -> None:
    if limit < 0:
        raise ValueError("limit must be nonnegative")
