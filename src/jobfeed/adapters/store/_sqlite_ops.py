"""SQLite implementations for company, enrichment, cost, state, and eval reads."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import aiosqlite

from jobfeed.adapters.store.sqlite_mapping import (
    evaluation_from_row,
    row_to_dict,
)
from jobfeed.adapters.store.sqlite_sql import MAX_STAGE_RETRIES
from jobfeed.domain.models import (
    AttentionItem,
    AttentionReport,
    CompanyRecord,
    CostEntry,
    DigestStats,
    JobEvaluation,
    MLGateResult,
)
from jobfeed.domain.quality import assess_quality


async def job_exists(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    platform: str,
    canonical_id: str,
) -> bool:
    """Check job existence by natural key.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        platform: Source platform.
        canonical_id: Platform-specific identity.

    Returns:
        True if the job exists.
    """
    async with lock:
        cursor = await db.execute(
            "SELECT 1 FROM jobs WHERE platform = ? AND canonical_id = ?",
            (platform, canonical_id),
        )
        return await cursor.fetchone() is not None


async def save_ml_gate_result(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    job_id: str,
    result: MLGateResult,
) -> None:
    """Persist ML gate decision and extracted features.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        job_id: Store-assigned job identity.
        result: Gate decision with feature fields.
    """
    domain_tags = json.dumps(result.domain_tags) if result.domain_tags else None
    tech_required = json.dumps(result.tech_required) if result.tech_required else None
    async with lock:
        await db.execute(
            """UPDATE jobs SET
                ml_gate_score = ?, ml_gate_result = ?, ml_gate_fail_reason = ?,
                ml_gate_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                ml_gate_version = ?, is_swe_role = ?, seniority_level = ?,
                degree_required = ?, clearance_required = ?, school_restricted = ?,
                yoe_min = ?, domain_tags = ?, tech_required = ?, role_type = ?
            WHERE id = ?""",
            (
                result.score,
                result.result,
                result.fail_reason,
                result.version,
                result.is_swe_role,
                result.seniority_level,
                result.degree_required,
                result.clearance_required,
                result.school_restricted,
                result.yoe_min,
                domain_tags,
                tech_required,
                result.role_type,
                int(job_id),
            ),
        )
        await db.commit()


async def mark_stage_b_skipped(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    job_id: str,
) -> None:
    """Mark Stage B as skipped below threshold.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        job_id: Store-assigned job identity.
    """
    async with lock:
        # Never overwrite a completed Stage B verdict: skipping is only valid
        # for rows that have not produced a Stage B result yet.
        await db.execute(
            """UPDATE evaluations SET
                stage_b_status = 'skipped_below_threshold',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE job_id = ?
              AND stage_b_status IS NOT 'completed'""",
            (int(job_id),),
        )
        await db.commit()


async def get_evaluation(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    job_id: str,
) -> JobEvaluation | None:
    """Fetch a single job's evaluation.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        job_id: Store-assigned job identity.

    Returns:
        Job evaluation if found, else None.
    """
    async with lock:
        cursor = await db.execute(
            """SELECT jobs.*, evaluations.stage_a_score, evaluations.stage_a_one_line,
                evaluations.stage_a_timing_eligible, evaluations.stage_a_status,
                evaluations.stage_a_error, evaluations.stage_a_model,
                evaluations.stage_a_cost_usd, evaluations.stage_a_prompt_hash,
                evaluations.stage_a_resume_hash, evaluations.stage_b_verdict,
                evaluations.stage_b_jd_summary, evaluations.stage_b_verdict_json,
                evaluations.stage_b_summary_json, evaluations.stage_b_fit_json,
                evaluations.stage_b_hooks_json, evaluations.stage_b_status,
                evaluations.stage_b_error, evaluations.stage_b_model,
                evaluations.stage_b_cost_usd, evaluations.stage_b_prompt_hash,
                evaluations.stage_b_resume_hash
            FROM jobs
            LEFT JOIN evaluations ON evaluations.job_id = jobs.id
            WHERE jobs.id = ?""",
            (int(job_id),),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return evaluation_from_row(row_to_dict(row))


async def top_evaluated_jobs(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    min_score: int = 0,
    limit: int = 100,
) -> list[JobEvaluation]:
    """Stage B-completed jobs sorted by score descending.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        min_score: Minimum stage_a_score.
        limit: Max results.

    Returns:
        Sorted evaluations.
    """
    async with lock:
        cursor = await db.execute(
            """SELECT jobs.*, evaluations.stage_a_score, evaluations.stage_a_one_line,
                evaluations.stage_a_timing_eligible, evaluations.stage_a_status,
                evaluations.stage_a_error, evaluations.stage_a_model,
                evaluations.stage_a_cost_usd, evaluations.stage_a_prompt_hash,
                evaluations.stage_a_resume_hash, evaluations.stage_b_verdict,
                evaluations.stage_b_jd_summary, evaluations.stage_b_verdict_json,
                evaluations.stage_b_summary_json, evaluations.stage_b_fit_json,
                evaluations.stage_b_hooks_json, evaluations.stage_b_status,
                evaluations.stage_b_error, evaluations.stage_b_model,
                evaluations.stage_b_cost_usd, evaluations.stage_b_prompt_hash,
                evaluations.stage_b_resume_hash
            FROM jobs
            JOIN evaluations ON evaluations.job_id = jobs.id
            WHERE evaluations.stage_b_status = 'completed'
              AND evaluations.stage_a_score >= ?
            ORDER BY evaluations.stage_a_score DESC
            LIMIT ?""",
            (min_score, limit),
        )
        rows = await cursor.fetchall()
    return [evaluation_from_row(row_to_dict(r)) for r in rows]


async def upsert_company(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    company: CompanyRecord,
) -> None:
    """Insert or update a company record.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        company: Company record to persist.
    """
    async with lock:
        await db.execute(
            """INSERT INTO companies (
                slug, ats_vendor, ats_override, last_verified_at,
                last_probe_attempt_at, job_count_last_scan, notes,
                consecutive_discover_failures
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                ats_vendor = COALESCE(excluded.ats_vendor, companies.ats_vendor),
                ats_override = excluded.ats_override,
                last_verified_at = COALESCE(excluded.last_verified_at,
                    companies.last_verified_at),
                last_probe_attempt_at = COALESCE(excluded.last_probe_attempt_at,
                    companies.last_probe_attempt_at),
                job_count_last_scan = excluded.job_count_last_scan,
                notes = COALESCE(excluded.notes, companies.notes),
                consecutive_discover_failures = excluded.consecutive_discover_failures
            """,
            (
                company.slug,
                company.ats_vendor,
                int(company.ats_override),
                (
                    company.last_verified_at.isoformat()
                    if company.last_verified_at
                    else None
                ),
                (
                    company.last_probe_attempt_at.isoformat()
                    if company.last_probe_attempt_at
                    else None
                ),
                company.job_count_last_scan,
                company.notes,
                company.consecutive_discover_failures,
            ),
        )
        await db.commit()


async def get_company(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    slug: str,
) -> CompanyRecord | None:
    """Load a company by slug.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        slug: Company slug.

    Returns:
        Company record if found, else None.
    """
    async with lock:
        cursor = await db.execute(
            "SELECT * FROM companies WHERE slug = ?",
            (slug,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    d = row_to_dict(row)
    return _company_from_row(d)


async def list_companies(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    vendor: str | None = None,
    include_removed: bool = False,
) -> list[CompanyRecord]:
    """List companies with optional filters.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        vendor: Filter by ATS vendor.
        include_removed: Include soft-deleted.

    Returns:
        Matching company records.
    """
    clauses: list[str] = []
    params: list[object] = []
    if not include_removed:
        clauses.append("(ats_vendor IS NULL OR ats_vendor != 'removed')")
    if vendor:
        clauses.append("ats_vendor = ?")
        params.append(vendor)
    where = " AND ".join(clauses) if clauses else "1=1"
    async with lock:
        cursor = await db.execute(
            f"SELECT * FROM companies WHERE {where} ORDER BY slug",
            params,
        )
        rows = await cursor.fetchall()
    return [_company_from_row(row_to_dict(r)) for r in rows]


async def mark_company_removed(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    slug: str,
) -> bool:
    """Soft-delete a company.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        slug: Company slug.

    Returns:
        True if a row was matched.
    """
    async with lock:
        cursor = await db.execute(
            """UPDATE companies SET
                ats_vendor = 'removed', ats_override = 0, last_verified_at = NULL
            WHERE slug = ?""",
            (slug,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def bump_discover_failure(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    slug: str,
) -> int:
    """Increment consecutive discover-failure counter.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        slug: Company slug.

    Returns:
        New failure count.
    """
    async with lock:
        await db.execute(
            """UPDATE companies
            SET consecutive_discover_failures = consecutive_discover_failures + 1
            WHERE slug = ?""",
            (slug,),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT consecutive_discover_failures FROM companies WHERE slug = ?",
            (slug,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def reset_discover_failures(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    slug: str,
) -> None:
    """Zero the discover-failure counter.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        slug: Company slug.
    """
    async with lock:
        await db.execute(
            "UPDATE companies SET consecutive_discover_failures = 0 WHERE slug = ?",
            (slug,),
        )
        await db.commit()


async def get_state(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    key: str,
) -> str | None:
    """Read a key-value state entry.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        key: State key.

    Returns:
        Value if found, else None.
    """
    async with lock:
        cursor = await db.execute(
            "SELECT value FROM state WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
    return str(row[0]) if row else None


async def set_state(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    key: str,
    value: str,
) -> None:
    """Write a key-value state entry.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        key: State key.
        value: State value.
    """
    async with lock:
        await db.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def record_cost(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    day: str,
    spent_usd: float,
) -> None:
    """Upsert daily cost ledger (calls +1 per invocation).

    Args:
        db: SQLite connection.
        lock: Connection lock.
        day: YYYY-MM-DD date string.
        spent_usd: Cost to accumulate.
    """
    async with lock:
        await db.execute(
            """INSERT INTO cost_ledger (day, spent_usd, calls)
            VALUES (?, ?, 1)
            ON CONFLICT(day) DO UPDATE SET
                spent_usd = cost_ledger.spent_usd + excluded.spent_usd,
                calls = cost_ledger.calls + 1,
                last_updated = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
            (day, spent_usd),
        )
        await db.commit()


async def get_cost(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    day: str,
) -> CostEntry | None:
    """Read a single day's cost entry.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        day: YYYY-MM-DD date.

    Returns:
        Cost entry if found, else None.
    """
    async with lock:
        cursor = await db.execute(
            "SELECT * FROM cost_ledger WHERE day = ?",
            (day,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    d = row_to_dict(row)
    return CostEntry(
        day=str(d["day"]),
        spent_usd=float(str(d["spent_usd"])),
        calls=int(str(d["calls"])),
        last_updated=datetime.fromisoformat(str(d["last_updated"])),
    )


async def get_cost_range(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    since_days: int = 30,
) -> list[CostEntry]:
    """Read cost entries within a date range.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        since_days: Days to look back.

    Returns:
        Cost entries ordered by day descending.
    """
    async with lock:
        cursor = await db.execute(
            "SELECT * FROM cost_ledger WHERE day >= date('now', ?) ORDER BY day DESC",
            (f"-{since_days} days",),
        )
        rows = await cursor.fetchall()
    results: list[CostEntry] = []
    for r in rows:
        d = row_to_dict(r)
        results.append(
            CostEntry(
                day=str(d["day"]),
                spent_usd=float(str(d["spent_usd"])),
                calls=int(str(d["calls"])),
                last_updated=datetime.fromisoformat(str(d["last_updated"])),
            )
        )
    return results


async def digest_stats(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    threshold: int = 60,
) -> DigestStats:
    """Aggregate counts for digest footer.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        threshold: Score threshold for filtered_count.

    Returns:
        Digest statistics.
    """
    async with lock:
        total = await _count(db, "SELECT COUNT(*) FROM jobs")
        scored_today = await _count(
            db,
            "SELECT COUNT(*) FROM jobs "
            "WHERE date(discovered_at, 'localtime') = date('now', 'localtime')",
        )
        stage_b_evaluated = await _count(
            db,
            "SELECT COUNT(*) FROM evaluations WHERE stage_b_status = 'completed'",
        )
        filtered_count = await _count(
            db,
            "SELECT COUNT(*) FROM evaluations "
            "WHERE stage_b_status = 'completed' "
            "AND CAST(json_extract(stage_b_fit_json, '$.score_0_100') AS INTEGER) >= ?",
            (threshold,),
        )
        cost_row = await (
            await db.execute(
                "SELECT calls, spent_usd FROM cost_ledger "
                "WHERE day = date('now', 'localtime')",
            )
        ).fetchone()
    llm_calls = int(cost_row[0]) if cost_row else 0
    cost_usd = float(cost_row[1]) if cost_row else 0.0
    return DigestStats(
        total_jobs=total,
        scored_today=scored_today,
        stage_b_evaluated=stage_b_evaluated,
        filtered_count=filtered_count,
        llm_calls_today=llm_calls,
        total_cost_today_usd=cost_usd,
    )


async def needs_attention(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    days: int = 7,
    max_per_category: int = 10,
) -> AttentionReport:
    """Surface pipeline health concerns.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        days: Look-back window.
        max_per_category: Max items per category.

    Returns:
        Attention report.
    """
    async with lock:
        enrich_cursor = await db.execute(
            """SELECT id, title, company, enrich_error FROM jobs
            WHERE enrich_error IS NOT NULL
            AND julianday(discovered_at) >= julianday(datetime('now', ?))
            LIMIT ?""",
            (f"-{days} days", max_per_category),
        )
        enrich_rows = await enrich_cursor.fetchall()

        low_q_cursor = await db.execute(
            """SELECT j.id, j.title, j.company, j.jd_quality FROM jobs j
            JOIN evaluations e ON e.job_id = j.id
            WHERE j.jd_quality IN ('stub', 'partial')
            AND e.stage_a_status = 'completed'
            AND julianday(j.discovered_at) >= julianday(datetime('now', ?))
            LIMIT ?""",
            (f"-{days} days", max_per_category),
        )
        low_q_rows = await low_q_cursor.fetchall()

        # Jobs stuck in error past the retry cap (any age — they no longer retry).
        stuck_cursor = await db.execute(
            """SELECT j.id, j.title, j.company,
                      e.stage_a_error_count, e.stage_b_error_count
               FROM jobs j
               JOIN evaluations e ON e.job_id = j.id
               WHERE e.stage_a_error_count >= ? OR e.stage_b_error_count >= ?
               LIMIT ?""",
            (MAX_STAGE_RETRIES, MAX_STAGE_RETRIES, max_per_category),
        )
        stuck_rows = await stuck_cursor.fetchall()

    enrich_errors = [
        AttentionItem(
            job_id=str(r[0]),
            title=str(r[1]),
            company=str(r[2]),
            category="enrich_error",
            detail=str(r[3]),
        )
        for r in enrich_rows
    ]
    low_quality = [
        AttentionItem(
            job_id=str(r[0]),
            title=str(r[1]),
            company=str(r[2]),
            category="low_quality_scored",
            detail=f"quality={r[3]}",
        )
        for r in low_q_rows
    ]
    stuck_scoring = [
        AttentionItem(
            job_id=str(r[0]),
            title=str(r[1]),
            company=str(r[2]),
            category="stuck_scoring",
            detail=f"stage_a_errors={r[3]}, stage_b_errors={r[4]}",
        )
        for r in stuck_rows
    ]
    return AttentionReport(
        enrich_errors=enrich_errors,
        low_quality_scored=low_quality,
        stuck_scoring=stuck_scoring,
    )


async def record_enrichment(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    job_id: str,
    jd_text: str,
    jd_quality: str,
    enriched_at: datetime,
    enrich_source: str,
    jd_lang: str | None = None,
) -> None:
    """Stamp a job as enriched with JD body and quality.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        job_id: Store-assigned job identity.
        jd_text: JD body text.
        jd_quality: Quality band string.
        enriched_at: Enrichment timestamp.
        enrich_source: Source label.
        jd_lang: Optional detected language.
    """
    async with lock:
        await db.execute(
            """UPDATE jobs SET
                jd_text = ?, jd_quality = ?, enriched_at = ?,
                enrich_source = ?, jd_lang = ?, enrich_error = NULL
            WHERE id = ?""",
            (
                jd_text,
                jd_quality,
                enriched_at.isoformat(),
                enrich_source,
                jd_lang,
                int(job_id),
            ),
        )
        await db.commit()


async def enrich_paste(
    db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    platform: str,
    canonical_id: str,
    jd_text: str,
) -> str:
    """Manual JD paste fallback.

    Args:
        db: SQLite connection.
        lock: Connection lock.
        platform: Source platform.
        canonical_id: Platform-specific identity.
        jd_text: Pasted JD text.

    Returns:
        Store-assigned job identity.

    Raises:
        ValueError: If job not found.
    """
    async with lock:
        cursor = await db.execute(
            "SELECT id FROM jobs WHERE platform = ? AND canonical_id = ?",
            (platform, canonical_id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"job not found: {platform}/{canonical_id}")
    job_id = str(row[0])
    quality = assess_quality(jd_text)
    await record_enrichment(
        db,
        lock,
        job_id=job_id,
        jd_text=jd_text,
        jd_quality=quality.value,
        enriched_at=datetime.now(tz=UTC),
        enrich_source="manual-paste",
    )
    return job_id


def _company_from_row(d: dict[str, object]) -> CompanyRecord:
    return CompanyRecord(
        slug=str(d["slug"]),
        ats_vendor=str(d["ats_vendor"]) if d["ats_vendor"] else None,
        ats_override=bool(d["ats_override"]),
        last_verified_at=(
            datetime.fromisoformat(str(d["last_verified_at"]))
            if d["last_verified_at"]
            else None
        ),
        last_probe_attempt_at=(
            datetime.fromisoformat(str(d["last_probe_attempt_at"]))
            if d["last_probe_attempt_at"]
            else None
        ),
        job_count_last_scan=int(str(d["job_count_last_scan"])),
        consecutive_discover_failures=int(str(d["consecutive_discover_failures"])),
        notes=str(d["notes"]) if d["notes"] else None,
    )


async def _count(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[object, ...] = (),
) -> int:
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    return int(row[0]) if row else 0
