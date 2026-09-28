"""Historical priority schema remains compatible after runtime retirement."""

import sqlite3
from pathlib import Path

from jobfeed.adapters.store.sqlite import SQLiteStore


async def test_existing_v1_with_external_queue_gets_additive_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade.db"
    store = SQLiteStore(path)
    await store.connect()
    await store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_job_priority_snapshot_page")
        connection.execute("DROP TABLE job_priority_snapshot")
        connection.execute(
            "CREATE TABLE application_queue (id INTEGER PRIMARY KEY, state TEXT)"
        )
        connection.execute(
            "CREATE INDEX idx_application_queue_state ON application_queue(state)"
        )

    reopened = SQLiteStore(path)
    await reopened.connect()
    try:
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM job_priority_snapshot"
            ).fetchone() == (0,)
    finally:
        await reopened.close()
