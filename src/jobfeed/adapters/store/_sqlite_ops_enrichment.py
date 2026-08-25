"""SQLite job enrichment writes, queues, and source lookup snapshots."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.adapters.store._sqlite_capability_support import _fetch_row, _fetch_rows
from jobfeed.adapters.store._sqlite_values import _datetime_from_text, _utc_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.ml_features import classify_role_type
from jobfeed.domain.models import QualityBand, UnenrichedJob
from jobfeed.domain.quality import assess_quality
from jobfeed.ports.source import StoredEnrichment

STALE_BACKFILL_MARKER = "backfill:stale-no-jd"


async def _record_enrichment(  # noqa: PLR0913
    lifecycle: SqliteLifecycle,
    *,
    job_id: str,
    jd_text: str,
    jd_quality: str,
    enriched_at: datetime,
    enrich_source: str,
    jd_lang: str | None,
    posted_at: datetime | None,
) -> None:
    numeric_id = int(job_id)
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection, "SELECT title FROM jobs WHERE id=?", (numeric_id,)
        )
        if row is None:
            raise ValueError(f"job not found: {job_id}")
        await connection.execute(
            """UPDATE jobs SET jd_text=?,jd_quality=?,enriched_at=?,
                enrich_source=?,jd_lang=?,enrich_error=NULL,closed_at=NULL,
                posted_at=COALESCE(posted_at,?),ml_gate_score=NULL,
                ml_gate_result=NULL,ml_gate_fail_reason=NULL,ml_gate_at=NULL,
                ml_gate_version=NULL,role_type=? WHERE id=?""",
            (
                jd_text,
                jd_quality,
                _utc_text(enriched_at),
                enrich_source,
                jd_lang,
                _utc_text(posted_at) if posted_at is not None else None,
                classify_role_type(str(row["title"]), jd_text),
                numeric_id,
            ),
        )


async def _list_unenriched_jobs(
    lifecycle: SqliteLifecycle,
    *,
    platform: str,
    limit: int,
) -> list[UnenrichedJob]:
    async with lifecycle.connection() as connection:
        rows = await _fetch_rows(
            connection,
            "SELECT id,canonical_id,url FROM jobs WHERE platform=? "
            "AND jd_text IS NULL AND closed_at IS NULL "
            "ORDER BY discovered_at DESC,id DESC LIMIT ?",
            (platform, limit),
        )
    return [
        UnenrichedJob(
            job_id=str(row["id"]),
            canonical_id=str(row["canonical_id"]),
            url=str(row["url"]),
        )
        for row in rows
    ]


async def _mark_job_closed(
    lifecycle: SqliteLifecycle,
    *,
    job_id: str,
    closed_at: datetime,
    reason: str | None,
) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE jobs SET closed_at=?,enrich_error=COALESCE(?,enrich_error) "
            "WHERE id=?",
            (_utc_text(closed_at), reason, int(job_id)),
        )


async def _enrich_paste(
    lifecycle: SqliteLifecycle,
    *,
    platform: str,
    canonical_id: str,
    jd_text: str,
) -> str:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection,
            "SELECT id FROM jobs WHERE platform=? AND canonical_id=?",
            (platform, canonical_id),
        )
    if row is None:
        raise ValueError(f"job not found: {platform}/{canonical_id}")
    job_id = str(row["id"])
    await _record_enrichment(
        lifecycle,
        job_id=job_id,
        jd_text=jd_text,
        jd_quality=assess_quality(jd_text).value,
        enriched_at=datetime.now(UTC),
        enrich_source="manual-paste",
        jd_lang=None,
        posted_at=None,
    )
    return job_id


async def _get_enrichment(
    lifecycle: SqliteLifecycle,
    *,
    platform: str,
    canonical_id: str,
) -> StoredEnrichment | None:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection,
            "SELECT jd_text,jd_quality,enriched_at,enrich_source FROM jobs "
            "WHERE platform=? AND canonical_id=?",
            (platform, canonical_id),
        )
    if row is None:
        return None
    quality = row["jd_quality"]
    return StoredEnrichment(
        jd_text=row["jd_text"],
        quality=QualityBand(quality) if quality else None,
        enriched_at=_datetime_from_text(row["enriched_at"]),
        enrich_source=row["enrich_source"],
    )


async def _get_closed_canonical_ids(
    lifecycle: SqliteLifecycle,
    *,
    platform: str,
) -> set[str]:
    async with lifecycle.connection() as connection:
        rows = await _fetch_rows(
            connection,
            "SELECT canonical_id FROM jobs WHERE platform=? AND closed_at IS NOT NULL "
            "AND (enrich_error IS NULL OR enrich_error<>?)",
            (platform, STALE_BACKFILL_MARKER),
        )
    return {str(row["canonical_id"]) for row in rows}
