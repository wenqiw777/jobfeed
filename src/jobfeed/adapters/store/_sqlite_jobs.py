"""SQLite jobs persistence with quality-aware natural-key upserts."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import aiosqlite

from jobfeed.adapters.store._normalize import normalize, normalize_company
from jobfeed.adapters.store._sqlite_real_job_evaluation import (
    sync_sqlite_real_job_input,
)
from jobfeed.adapters.store._sqlite_real_job_identity import resolve_sqlite_real_job
from jobfeed.adapters.store._sqlite_values import (
    _canonical_json,
    _job_from_row,
    _utc_now_text,
    _utc_text,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.ml_features import classify_role_type
from jobfeed.domain.models import JobPosting, MLGateResult, SaveJobResult
from jobfeed.domain.quality import quality_rank

_INSERT_JOB_SQL = """INSERT INTO jobs (
    platform, canonical_id, url, title, company, location,
    jd_text, jd_quality, posted_at, discovered_at, enriched_at, enrich_source,
    company_norm, title_norm, location_norm, closed_at, enrich_error, role_type,
    external_identity, enrich_attempted_at, enrich_error_code, enrich_retry_after,
    is_repost, repost_evidence, repost_observed_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id"""


async def _save_job(lifecycle: SqliteLifecycle, job: JobPosting) -> SaveJobResult:
    """Atomically insert or quality-aware update one natural-key job."""
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute("BEGIN IMMEDIATE")
        try:
            result = await _save_job_on_connection(connection, job)
            await connection.commit()
            return result
        except BaseException:
            await connection.rollback()
            raise


async def _save_job_on_connection(
    connection: aiosqlite.Connection, job: JobPosting
) -> SaveJobResult:
    existing = await _job_by_natural_key(connection, job)
    if existing is not None:
        await _update_job(connection, job, existing)
        if await _has_real_job_schema(connection):
            await _ensure_real_job(connection, int(existing["id"]), job)
            await resolve_sqlite_real_job(connection, int(existing["id"]), job)
            await sync_sqlite_real_job_input(connection, int(existing["id"]))
        return SaveJobResult(job_id=str(existing["id"]), inserted=False, updated=True)
    cursor = await connection.execute(_INSERT_JOB_SQL, _job_values(job))
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise RuntimeError("SQLite job insert returned no identity")
    if await _has_real_job_schema(connection):
        await _ensure_real_job(connection, int(row["id"]), job)
        await resolve_sqlite_real_job(connection, int(row["id"]), job)
        await sync_sqlite_real_job_input(connection, int(row["id"]))
    return SaveJobResult(job_id=str(row["id"]), inserted=True, updated=False)


async def _has_real_job_schema(connection: aiosqlite.Connection) -> bool:
    cursor = await connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='real_jobs'"
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def _ensure_real_job(
    connection: aiosqlite.Connection, source_id: int, job: JobPosting
) -> None:
    """Attach a source to one parent in the surrounding source-write transaction."""
    if job.apply_url:
        await connection.execute(
            "UPDATE jobs SET apply_url=? WHERE id=?", (job.apply_url, source_id)
        )
    cursor = await connection.execute(
        "SELECT real_job_id FROM jobs WHERE id=?", (source_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise RuntimeError("source posting disappeared during save")
    real_job_id = row[0]
    if real_job_id is None:
        cursor = await connection.execute(
            "INSERT INTO real_jobs(representative_job_id) VALUES(?) RETURNING id",
            (source_id,),
        )
        parent = await cursor.fetchone()
        await cursor.close()
        if parent is None:
            raise RuntimeError("real job insert returned no identity")
        real_job_id = int(parent[0])
        await connection.execute(
            "UPDATE jobs SET real_job_id=? WHERE id=?", (real_job_id, source_id)
        )
    await connection.execute(
        "INSERT OR IGNORE INTO real_job_status("
        "real_job_id,status,next_followup_at,resume_variant,notes,"
        "last_status_change_at) "
        "SELECT ?,status,next_followup_at,resume_variant,notes,last_status_change_at "
        "FROM job_status WHERE job_id=?",
        (real_job_id, source_id),
    )
    await connection.execute(
        "INSERT OR IGNORE INTO real_job_status_history("
        "real_job_id,source_history_id,from_status,to_status,changed_at,reason,"
        "resume_variant_at_change) SELECT ?,id,from_status,to_status,changed_at,"
        "reason,resume_variant_at_change FROM job_status_history WHERE job_id=?",
        (real_job_id, source_id),
    )
    await connection.execute(
        "INSERT OR IGNORE INTO real_job_identifiers("
        "real_job_id,provider,scope,native_id,evidence_job_id,observed_url) "
        "VALUES(?,?,?,?,?,?)",
        (real_job_id, job.platform, "", job.canonical_id, source_id, job.url),
    )


async def _save_job_batch(  # noqa: PLR0913 - explicit transaction fence
    lifecycle: SqliteLifecycle,
    jobs: list[JobPosting],
    *,
    receipt_key: str,
    run_id: str,
    owner_id: str,
    generation: int,
) -> list[SaveJobResult]:
    """Commit jobs and their original outcomes atomically under the live run fence."""
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = await connection.execute(
                "SELECT 1 FROM run_leases WHERE kind='scan' AND run_id=? "
                "AND owner_id=? AND generation=? AND expires_at>?",
                (run_id, owner_id, generation, _utc_now_text()),
            )
            lease = await cursor.fetchone()
            await cursor.close()
            if lease is None:
                raise RuntimeError("Scan write lease lost")
            cursor = await connection.execute(
                "SELECT value FROM state WHERE key=?", (receipt_key,)
            )
            receipt = await cursor.fetchone()
            await cursor.close()
            if receipt:
                results = [
                    SaveJobResult(**item) for item in json.loads(receipt["value"])
                ]
            else:
                results = [
                    await _save_job_on_connection(connection, job) for job in jobs
                ]
                await connection.execute(
                    "INSERT INTO state(key,value) VALUES(?,?)",
                    (receipt_key, json.dumps([asdict(result) for result in results])),
                )
            await connection.commit()
            return results
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


async def _get_jobs_by_canonical_ids(
    lifecycle: SqliteLifecycle, *, platform: str, canonical_ids: list[str]
) -> dict[str, JobPosting]:
    """Probe a bounded discovery page with one connection, preserving source IDs.

    Time complexity: O(N + R) Python work for N requested IDs and R returned
    rows. Each ID belongs to one fixed-size SQL batch and each row is read once.
    """
    ids = list(dict.fromkeys(canonical_ids))
    jobs: dict[str, JobPosting] = {}
    if not ids:
        return jobs
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        for offset in range(0, len(ids), 100):
            batch = ids[offset : offset + 100]
            placeholders = ",".join("?" for _ in batch)
            cursor = await connection.execute(
                "SELECT * FROM jobs WHERE platform=? "
                f"AND canonical_id IN ({placeholders})",
                (platform, *batch),
            )
            for row in await cursor.fetchall():
                job = _job_from_row(row)
                jobs[job.canonical_id] = job
            await cursor.close()
    return jobs


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


async def _save_hard_filters(
    lifecycle: SqliteLifecycle,
    reasons: dict[str, str],
) -> None:
    """Persist one deterministic exclusion reason per numeric job id."""
    rows = [(reason, int(job_id)) for job_id, reason in reasons.items()]
    if not rows:
        return
    async with lifecycle.connection() as connection:
        await connection.executemany(
            "UPDATE jobs SET hard_filter=? WHERE id=?",
            rows,
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
        job.external_identity or external_identity(job.url),
        _time(job.enrich_attempted_at),
        job.enrich_error_code,
        _time(job.enrich_retry_after),
        _bool_int(job.is_repost),
        job.repost_evidence,
        _time(job.repost_observed_at),
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
    company = (
        existing["company"]
        if job.platform == "linkedin" and job.company.strip().lower() in {"", "unknown"}
        else job.company
    )
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
    hard_filter_input_changed = (
        company != existing["company"]
        or job.location != existing["location"]
        or (_time(job.posted_at) or existing["posted_at"]) != existing["posted_at"]
    )
    role_type = classify_role_type(job.title, jd_text or "")
    closed_at = (
        None
        if job.jd_text is not None
        else existing["closed_at"] or _time(job.closed_at)
    )
    enrich_error = (
        None
        if job.jd_text is not None and job.jd_quality in {"good", "full"}
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
    hard_filter_sql = ", hard_filter=NULL" if hard_filter_input_changed else ""
    await connection.execute(
        """UPDATE jobs SET url=?, title=?, company=?, location=?, jd_text=?,
            jd_quality=?, posted_at=?, discovered_at=?, enriched_at=?, enrich_source=?,
            company_norm=?, title_norm=?, location_norm=?, closed_at=?,
            enrich_error=?, role_type=?, external_identity=?,
            enrich_attempted_at=?, enrich_error_code=?, enrich_retry_after=?,
            is_repost=?, repost_evidence=?, repost_observed_at=?"""
        + gate_sql
        + hard_filter_sql
        + " WHERE id=?",
        (
            job.url,
            job.title,
            company,
            job.location,
            jd_text,
            jd_quality,
            _time(job.posted_at) or existing["posted_at"],
            existing["discovered_at"],
            _time(job.enriched_at) or existing["enriched_at"],
            enrich_source,
            normalize_company(company),
            normalize(job.title),
            normalize(job.location),
            closed_at,
            enrich_error,
            role_type,
            job.external_identity or external_identity(job.url),
            _time(job.enrich_attempted_at) or existing["enrich_attempted_at"],
            job.enrich_error_code,
            _time(job.enrich_retry_after),
            *_repost_values(job, existing),
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


def _repost_values(job: JobPosting, existing: aiosqlite.Row) -> tuple[object, ...]:
    if job.is_repost is None or (
        existing["is_repost"] == 1 and job.is_repost is not True
    ):
        return (
            existing["is_repost"],
            existing["repost_evidence"],
            existing["repost_observed_at"],
        )
    return (
        _bool_int(job.is_repost),
        job.repost_evidence,
        _time(job.repost_observed_at),
    )
