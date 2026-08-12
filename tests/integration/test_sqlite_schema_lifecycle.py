"""End-to-end contracts for the Task 1 SQLite schema and lifecycle slice."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import (
    SQLITE_TABLE_NAMES,
    ensure_sqlite_schema,
)


async def test_schema_lifecycle_backup_and_restore_round_trip(tmp_path: Path) -> None:
    """A real file preserves the v1 schema and committed data through restore."""
    database_path = tmp_path / "jobfeed.db"
    backup_path = tmp_path / "jobfeed.backup.db"
    lifecycle = SqliteLifecycle(database_path, ensure_sqlite_schema)

    await lifecycle.open()
    async with lifecycle.connection() as connection:
        await connection.execute(
            "INSERT INTO state(key, value) VALUES('checkpoint', 'before-backup')"
        )
        tables = await _column(
            connection,
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
        leases = await _rows(
            connection,
            "SELECT kind, generation, owner_id, run_id, heartbeat_at, expires_at "
            "FROM run_leases ORDER BY kind",
        )
    assert tables == sorted(SQLITE_TABLE_NAMES)
    assert leases == [
        ("evaluate", 0, None, None, None, None),
        ("scan", 0, None, None, None, None),
    ]

    assert await lifecycle.backup_to(backup_path) == backup_path
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE state SET value='after-backup' WHERE key='checkpoint'"
        )
    await lifecycle.close()

    await lifecycle.restore_from(backup_path)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        assert (
            await _scalar(
                connection,
                "SELECT value FROM state WHERE key='checkpoint'",
            )
            == "before-backup"
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                "INSERT INTO evaluations(job_id) VALUES(999999999)"
            )
    await lifecycle.close()


async def _rows(
    connection: aiosqlite.Connection,
    statement: str,
) -> list[tuple[object, ...]]:
    cursor = await connection.execute(statement)
    rows = await cursor.fetchall()
    await cursor.close()
    return rows


async def _column(
    connection: aiosqlite.Connection,
    statement: str,
) -> list[object]:
    return [row[0] for row in await _rows(connection, statement)]


async def _scalar(connection: aiosqlite.Connection, statement: str) -> object:
    rows = await _rows(connection, statement)
    assert len(rows) == 1
    return rows[0][0]
