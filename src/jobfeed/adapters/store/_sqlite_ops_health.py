"""SQLite pipeline attention and stale-job maintenance operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import _fetch_rows
from jobfeed.adapters.store._sqlite_values import _utc_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import AttentionItem, AttentionReport
from jobfeed.domain.scoring import MAX_STAGE_RETRIES

STALE_BACKFILL_MARKER = "backfill:stale-no-jd"


async def _needs_attention(
    lifecycle: SqliteLifecycle,
    *,
    days: int,
    max_per_category: int,
) -> AttentionReport:
    cutoff = _utc_text(_now() - timedelta(days=days))
    async with lifecycle.connection() as connection:
        enrich_rows = await _fetch_rows(
            connection,
            "SELECT id,title,company,enrich_error FROM jobs "
            "WHERE enrich_error IS NOT NULL AND discovered_at>=? LIMIT ?",
            (cutoff, max_per_category),
        )
        quality_rows = await _fetch_rows(
            connection,
            "SELECT j.id,j.title,j.company,j.jd_quality FROM jobs j "
            "JOIN evaluations e ON e.job_id=j.id WHERE "
            "j.jd_quality IN ('stub','partial') AND "
            "e.stage_a_status='completed' AND j.discovered_at>=? LIMIT ?",
            (cutoff, max_per_category),
        )
        stuck_rows = await _fetch_rows(
            connection,
            "SELECT j.id,j.title,j.company,e.stage_a_error_count,"
            "e.stage_b_error_count FROM jobs j JOIN evaluations e ON "
            "e.job_id=j.id WHERE e.stage_a_error_count>=? OR "
            "e.stage_b_error_count>=? LIMIT ?",
            (MAX_STAGE_RETRIES, MAX_STAGE_RETRIES, max_per_category),
        )
    return AttentionReport(
        enrich_errors=[
            _attention(row, "enrich_error", str(row["enrich_error"]))
            for row in enrich_rows
        ],
        low_quality_scored=[
            _attention(row, "low_quality_scored", f"quality={row['jd_quality']}")
            for row in quality_rows
        ],
        stuck_scoring=[
            _attention(
                row,
                "stuck_scoring",
                f"stage_a_errors={row['stage_a_error_count']}, "
                f"stage_b_errors={row['stage_b_error_count']}",
            )
            for row in stuck_rows
        ],
    )


async def _mark_stale_jobs_closed(
    lifecycle: SqliteLifecycle,
    *,
    older_than_days: int,
    dry_run: bool,
) -> int:
    if older_than_days < 1:
        raise ValueError("older_than_days must be >= 1")
    now = _now()
    cutoff = _utc_text(now - timedelta(days=older_than_days))
    where = (
        "(jd_quality IS NULL OR jd_quality IN ('missing','abandoned')) "
        "AND discovered_at<? AND closed_at IS NULL"
    )
    async with lifecycle.connection() as connection:
        if dry_run:
            rows = await _fetch_rows(
                connection, f"SELECT id FROM jobs WHERE {where}", (cutoff,)
            )
            return len(rows)
        cursor = await connection.execute(
            "UPDATE jobs SET closed_at=?,enrich_error=? WHERE "
            + where
            + " RETURNING id",
            (_utc_text(now), STALE_BACKFILL_MARKER, cutoff),
        )
        rows = list(await cursor.fetchall())
        await cursor.close()
    return len(rows)


def _attention(
    row: aiosqlite.Row,
    category: str,
    detail: str,
) -> AttentionItem:
    return AttentionItem(
        job_id=str(row["id"]),
        title=str(row["title"]),
        company=str(row["company"]),
        category=category,
        detail=detail,
    )


def _now() -> datetime:
    return datetime.now(UTC)
