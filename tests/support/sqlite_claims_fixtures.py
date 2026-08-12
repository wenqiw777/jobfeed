"""Raw SQLite row fixtures for claims and run-lease integration tests."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite


def _sqlite_timestamp(value: datetime) -> str:
    """Encode an aware datetime as canonical SQLite UTC text."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _seed_job(  # noqa: PLR0913
    connection: aiosqlite.Connection,
    *,
    canonical_id: str,
    discovered_at: datetime,
    quality: str = "full",
    closed_at: datetime | None = None,
    company_norm: str | None = None,
    title_norm: str | None = None,
    gate_result: str | None = None,
) -> str:
    """Insert the minimum complete job row and return its identity."""
    cursor = await connection.execute(
        """INSERT INTO jobs (
               platform, canonical_id, url, title, company, location,
               discovered_at, jd_quality, closed_at, company_norm, title_norm,
               ml_gate_result
           ) VALUES ('test', ?, ?, ?, 'Example', 'Remote', ?, ?, ?, ?, ?, ?)
           RETURNING id""",
        (
            canonical_id,
            f"https://example.test/{canonical_id}",
            f"Role {canonical_id}",
            _sqlite_timestamp(discovered_at),
            quality,
            _sqlite_timestamp(closed_at) if closed_at else None,
            company_norm,
            title_norm,
            gate_result,
        ),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    return str(row[0])


async def _seed_evaluation(  # noqa: PLR0913
    connection: aiosqlite.Connection,
    *,
    job_id: str,
    updated_at: datetime,
    stage_a_status: str | None = None,
    stage_a_score: int | None = None,
    stage_a_error: str | None = None,
    stage_a_error_count: int = 0,
    stage_b_status: str | None = None,
    stage_b_verdict: str | None = None,
    stage_b_error: str | None = None,
    stage_b_error_count: int = 0,
) -> None:
    """Insert an evaluation row with explicit claim-relevant state."""
    timestamp = _sqlite_timestamp(updated_at)
    await connection.execute(
        """INSERT INTO evaluations (
               job_id, created_at, updated_at,
               stage_a_status, stage_a_score, stage_a_error, stage_a_error_count,
               stage_b_status, stage_b_verdict, stage_b_error, stage_b_error_count
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(job_id),
            timestamp,
            timestamp,
            stage_a_status,
            stage_a_score,
            stage_a_error,
            stage_a_error_count,
            stage_b_status,
            stage_b_verdict,
            stage_b_error,
            stage_b_error_count,
        ),
    )
