"""Independent-process concurrency contracts for PostgreSQL store operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from tests.support.pg_process_race import run_store_process_race

pytestmark = pytest.mark.postgres

WORKER_COUNT = 2


async def test_two_processes_save_same_natural_key_truthfully(
    store: PostgresStore,
    migrated_pg_url: str,
    tmp_path: Path,
) -> None:
    """Exactly one process inserts; the other updates the same stored row."""
    payload = {
        "operation": "save_job",
        "canonical_id": "same-natural-key",
        "company": "Same Company",
    }

    results = await run_store_process_race(
        dsn=migrated_pg_url,
        payloads=[payload.copy() for _ in range(WORKER_COUNT)],
        sync_dir=tmp_path / "save-job-race",
    )

    assert sum(result["inserted"] is True for result in results) == 1
    assert sum(result["updated"] is True for result in results) == 1
    assert len({result["job_id"] for result in results}) == 1
    assert await store.count_rows("jobs") == 1
