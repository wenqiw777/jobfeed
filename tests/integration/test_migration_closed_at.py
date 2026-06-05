"""Integration test: jobs.closed_at column added by migration 0005."""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_jobs_closed_at_column_exists_and_nullable(fresh_pg_dsn: str) -> None:
    """After migrating to head the jobs table has a nullable closed_at column.

    Args:
        fresh_pg_dsn: DSN of a freshly reset and migrated database.
    """
    conn = await asyncpg.connect(fresh_pg_dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'jobs'
              AND column_name  = 'closed_at'
            """
        )
    finally:
        await conn.close()

    assert row is not None, "jobs.closed_at column not found after migration"
    assert row["is_nullable"] == "YES", "jobs.closed_at should be nullable"
    assert "timestamp" in row["data_type"], (
        f"expected a timestamp type, got {row['data_type']!r}"
    )
