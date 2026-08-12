"""End-to-end contracts for the Task 1 SQLite schema and lifecycle slice."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite_lifecycle import (
    SqliteLifecycle,
    SqliteLifecycleStateError,
)
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


@pytest.mark.parametrize("invalid_source", ["unrelated_schema", "wrong_version"])
async def test_restore_rejects_non_jobfeed_schema_before_replacing_target(
    tmp_path: Path,
    invalid_source: str,
) -> None:
    """A valid SQLite file with the wrong schema cannot replace Jobfeed v1."""
    target_path = tmp_path / "jobfeed.db"
    source_path = tmp_path / f"{invalid_source}.db"
    lifecycle = SqliteLifecycle(target_path, ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        await connection.execute(
            "INSERT INTO state(key, value) VALUES('sentinel', 'keep-me')"
        )
    await lifecycle.close()
    target_before = target_path.read_bytes()
    _write_invalid_schema(source_path, invalid_source)

    with pytest.raises(
        ValueError,
        match=r"SQLite schema|unsupported SQLite schema",
    ):
        await lifecycle.restore_from(source_path)

    assert target_path.read_bytes() == target_before
    assert _restore_stages(tmp_path) == []
    with sqlite3.connect(target_path) as connection:
        assert connection.execute(
            "SELECT value FROM state WHERE key='sentinel'"
        ).fetchone() == ("keep-me",)


async def test_restore_rejects_initializer_with_active_transaction(
    tmp_path: Path,
) -> None:
    """Restore rolls back and cleans a stage whose initializer leaks a tx."""
    initializer_calls = 0

    async def initialize(connection: aiosqlite.Connection) -> None:
        nonlocal initializer_calls
        initializer_calls += 1
        if initializer_calls == 1:
            await connection.execute(
                "CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            await connection.execute(
                "INSERT INTO sentinel(id, value) VALUES(1, 'keep-me')"
            )
            return
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute("CREATE TABLE leaked_schema (id INTEGER)")

    target_path = tmp_path / "jobfeed.db"
    source_path = tmp_path / "source.db"
    lifecycle = SqliteLifecycle(target_path, initialize)
    await lifecycle.open()
    await lifecycle.close()
    target_before = target_path.read_bytes()
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO sentinel(id, value) VALUES(1, 'replacement')")

    with pytest.raises(SqliteLifecycleStateError, match="active transaction"):
        await lifecycle.restore_from(source_path)

    assert target_path.read_bytes() == target_before
    assert _restore_stages(tmp_path) == []
    with sqlite3.connect(target_path) as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone() == (
            "keep-me",
        )


def _write_invalid_schema(path: Path, invalid_source: str) -> None:
    with sqlite3.connect(path) as connection:
        if invalid_source == "unrelated_schema":
            connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            connection.execute("PRAGMA user_version=1")
            return
        connection.execute("PRAGMA user_version=999")


def _restore_stages(directory: Path) -> list[Path]:
    return sorted(directory.glob(".jobfeed.db.restore-*.tmp*"))


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
