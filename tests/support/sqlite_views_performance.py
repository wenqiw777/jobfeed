"""Fixtures for SQLite views and performance capability contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from jobfeed.adapters.store.sqlite_views_performance import SqliteViewsPerformance

from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.models import PipelineRun

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


async def open_views_performance(
    path: Path,
) -> tuple[SqliteLifecycle, SqliteViewsPerformance]:
    """Open one lifecycle and bind the views/performance capability."""
    lifecycle = SqliteLifecycle(path, ensure_sqlite_schema)
    await lifecycle.open()
    return lifecycle, SqliteViewsPerformance(lifecycle)


async def insert_job(  # noqa: PLR0913
    lifecycle: SqliteLifecycle,
    canonical_id: str,
    *,
    discovered_at: str,
    company: str = "Example",
    company_norm: str | None = "example",
    title: str = "Engineer",
    title_norm: str | None = "engineer",
    status: str = "new",
    posted_at: str | None = None,
    quality: str | None = "full",
    closed_at: str | None = None,
) -> int:
    """Insert a deterministic jobs-view row and return its numeric id."""
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            """INSERT INTO jobs (
                   platform, canonical_id, url, title, company, location,
                   jd_quality, posted_at, discovered_at, company_norm,
                   title_norm, location_norm, closed_at
               ) VALUES ('test',?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (
                canonical_id,
                f"https://example.test/{canonical_id}",
                title,
                company,
                "Remote",
                quality,
                posted_at,
                discovered_at,
                company_norm,
                title_norm,
                "remote",
                closed_at,
            ),
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None
        job_id = int(row[0])
        await connection.execute(
            "UPDATE job_status SET status=? WHERE job_id=?", (status, job_id)
        )
        return job_id


async def set_evaluation(  # noqa: PLR0913
    lifecycle: SqliteLifecycle,
    job_id: int,
    *,
    stage_a_score: int | None = None,
    fit_score: int | None = None,
    verdict: str | None = None,
    stage_b_status: str | None = None,
    stage_a_at: str | None = None,
) -> None:
    """Insert one evaluation summary used by view and insights tests."""
    fit_json = None if fit_score is None else f'{{"score_0_100":{fit_score}}}'
    async with lifecycle.connection() as connection:
        await connection.execute(
            """INSERT INTO evaluations (
                   job_id, stage_a_score, stage_a_status, stage_a_at,
                   stage_b_verdict, stage_b_fit_json, stage_b_status,
                   created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                stage_a_score,
                "completed" if stage_a_score is not None else None,
                stage_a_at,
                verdict,
                fit_json,
                stage_b_status,
                discovered_text(),
                discovered_text(),
            ),
        )


async def insert_run(lifecycle: SqliteLifecycle, run: PipelineRun) -> None:
    """Insert a complete pipeline run without acquiring a run lease."""
    async with lifecycle.connection() as connection:
        await connection.execute(
            """INSERT INTO pipeline_runs (
                   run_id, started_at, source, status, jobs_discovered,
                   jobs_inserted, jobs_updated, jobs_filtered, jobs_ml_gated,
                   jobs_gate_passed, stage_a_scored, stage_b_scored, jobs_scored,
                   total_llm_cost_usd, errors, finished_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run.run_id,
                utc_text(run.started_at),
                run.source,
                run.status,
                run.jobs_discovered,
                run.jobs_inserted,
                run.jobs_updated,
                run.jobs_filtered,
                run.jobs_ml_gated,
                run.jobs_gate_passed,
                run.stage_a_scored,
                run.stage_b_scored,
                run.jobs_scored,
                run.total_llm_cost_usd,
                run.errors,
                utc_text(run.finished_at) if run.finished_at else None,
            ),
        )


def utc_text(value: datetime) -> str:
    """Encode an aware fixture time using canonical SQLite UTC text."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def discovered_text() -> str:
    """Return the fixed fixture timestamp in canonical form."""
    return utc_text(NOW)


async def rows(
    lifecycle: SqliteLifecycle, statement: str, params: tuple[object, ...] = ()
) -> list[aiosqlite.Row]:
    """Fetch raw rows for transaction and ordering assertions."""
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(statement, params)
        result = list(await cursor.fetchall())
        await cursor.close()
        return result
