"""Canonical source dates stay materialized as source jobs change."""

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from jobfeed.adapters.store._sqlite_values import _utc_text
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting


@pytest.mark.asyncio
async def test_source_write_materializes_canonical_dates(tmp_path) -> None:
    database = tmp_path / "canonical-dates.db"
    store = SQLiteStore(database)
    await store.connect()
    discovered = datetime(2026, 9, 20, 12, tzinfo=UTC)
    posted = discovered - timedelta(days=2)
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="materialized-dates",
                url="https://example.test/materialized-dates",
                title="Engineer",
                company="Example",
                location="Remote",
                discovered_at=discovered,
                posted_at=posted,
            )
        )
        parent = await store.resolve_real_job_id(saved.job_id)
        assert parent is not None

        async with aiosqlite.connect(database) as connection:
            cursor = await connection.execute(
                "SELECT first_discovered_at,canonical_posted_at "
                "FROM real_jobs WHERE id=?",
                (int(parent),),
            )
            row = await cursor.fetchone()
            await cursor.close()

        assert row == (_utc_text(discovered), _utc_text(posted))
    finally:
        await store.close()
