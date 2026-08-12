"""SQLite v1 schema creation and version-gate tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)
from jobfeed.adapters.store import sqlite_schema
from jobfeed.adapters.store.sqlite_schema import (
    SQLITE_METADATA,
    SQLITE_SCHEMA_VERSION,
    SQLITE_TABLE_NAMES,
    ensure_sqlite_schema,
)

_RUN_LEASE_ROWS = [
    ("evaluate", 0, None, None, None, None),
    ("scan", 0, None, None, None, None),
]
_INJECTED_FAILURE_CALL = 4


async def _scalar(connection: aiosqlite.Connection, sql: str) -> Any:
    cursor = await connection.execute(sql)
    row = await cursor.fetchone()
    await cursor.close()
    return row[0] if row else None


async def _table_names(connection: aiosqlite.Connection) -> tuple[str, ...]:
    cursor = await connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return tuple(row[0] for row in rows)


def test_metadata_matches_frozen_0008_registry_plus_run_leases() -> None:
    """Core metadata has the exact migrated columns and one lease table."""
    migrated = CANONICAL_SCHEMA_MANIFEST_V1.tables

    assert SQLITE_SCHEMA_VERSION == 1
    expected_names = (*(table.name for table in migrated), "run_leases")
    assert expected_names == SQLITE_TABLE_NAMES
    assert tuple(SQLITE_METADATA.tables) == SQLITE_TABLE_NAMES
    for expected in migrated:
        actual = SQLITE_METADATA.tables[expected.name]
        assert tuple(column.name for column in actual.columns) == tuple(
            column.name for column in expected.columns
        )
        assert (
            tuple(column.name for column in actual.primary_key) == expected.primary_key
        )
        for expected_column in expected.columns:
            actual_column = actual.columns[expected_column.name]
            assert str(actual_column.type) == expected_column.target_sqlite_type
            assert actual_column.nullable is expected_column.nullable


@pytest.mark.asyncio
async def test_empty_database_migrates_atomically_to_v1_and_reopens() -> None:
    """A new database gets exactly 15 tables and two idle permanent leases."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == 1
        assert await _table_names(connection) == tuple(sorted(SQLITE_TABLE_NAMES))
        cursor = await connection.execute(
            "SELECT kind, generation, owner_id, run_id, heartbeat_at, expires_at "
            "FROM run_leases ORDER BY kind"
        )
        assert await cursor.fetchall() == _RUN_LEASE_ROWS
        await cursor.close()

        await connection.execute("INSERT INTO state(key, value) VALUES('kept', 'yes')")
        await connection.commit()
        await ensure_sqlite_schema(connection)
        assert (
            await _scalar(connection, "SELECT value FROM state WHERE key='kept'")
            == "yes"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [2, 99, 2_147_483_647])
async def test_unknown_schema_version_fails_closed(version: int) -> None:
    """Versions newer than v1 are never guessed or modified."""
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="unsupported SQLite schema version"):
            await ensure_sqlite_schema(connection)
        assert await _scalar(connection, "PRAGMA user_version") == version
        assert await _table_names(connection) == ()


@pytest.mark.asyncio
async def test_inconsistent_zero_or_v1_database_fails_closed() -> None:
    """Neither a partial unversioned DB nor a version-only DB is repaired."""
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("CREATE TABLE intruder(id INTEGER PRIMARY KEY)")
        await connection.commit()
        with pytest.raises(ValueError, match="version 0 database is not empty"):
            await ensure_sqlite_schema(connection)
        assert await _table_names(connection) == ("intruder",)

    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("PRAGMA user_version=1")
        with pytest.raises(ValueError, match="schema v1 is inconsistent"):
            await ensure_sqlite_schema(connection)
        assert await _table_names(connection) == ()


@pytest.mark.asyncio
async def test_schema_migration_rejects_an_active_caller_transaction() -> None:
    """Schema creation never commits or rolls back an outer transaction."""
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="requires no active transaction"):
            await ensure_sqlite_schema(connection)
        assert connection.in_transaction
        await connection.rollback()


@pytest.mark.asyncio
async def test_two_connections_serialize_empty_schema_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two starters that both observe version zero converge on exact v1."""
    database_path = tmp_path / "concurrent-schema.db"
    contender = await aiosqlite.connect(database_path)
    migrator = await aiosqlite.connect(database_path)
    await contender.execute("PRAGMA busy_timeout=5000")
    await migrator.execute("PRAGMA busy_timeout=5000")
    original_user_objects = sqlite_schema._user_objects
    prelock_check_reached = asyncio.Event()
    migration_finished = asyncio.Event()

    async def hold_legacy_prelock_check(
        connection: aiosqlite.Connection,
    ) -> tuple[str, ...]:
        objects = await original_user_objects(connection)
        if connection is contender and not connection.in_transaction:
            prelock_check_reached.set()
            await migration_finished.wait()
        return objects

    monkeypatch.setattr(sqlite_schema, "_user_objects", hold_legacy_prelock_check)
    contender_task = asyncio.create_task(ensure_sqlite_schema(contender))
    prelock_task = asyncio.create_task(prelock_check_reached.wait())
    try:
        done, _ = await asyncio.wait(
            {contender_task, prelock_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if prelock_task in done:
            await ensure_sqlite_schema(migrator)
            migration_finished.set()
        else:
            prelock_task.cancel()
            await contender_task
            await ensure_sqlite_schema(migrator)
        await contender_task

        assert await _scalar(contender, "PRAGMA user_version") == 1
        assert await _scalar(migrator, "PRAGMA user_version") == 1
        assert await _table_names(contender) == tuple(sorted(SQLITE_TABLE_NAMES))
        assert await _table_names(migrator) == tuple(sorted(SQLITE_TABLE_NAMES))
    finally:
        migration_finished.set()
        prelock_task.cancel()
        await contender.close()
        await migrator.close()


@pytest.mark.asyncio
async def test_v1_reopen_accepts_occupied_leases_but_rejects_schema_tampering() -> None:
    """Runtime lease state is data; missing DDL or permanent rows are corruption."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute(
            "UPDATE run_leases SET generation=1, owner_id='owner', run_id='run', "
            "heartbeat_at='2026-08-12T00:00:00Z', "
            "expires_at='2026-08-12T00:03:00Z' WHERE kind='scan'"
        )
        await connection.commit()
        await ensure_sqlite_schema(connection)

        await connection.execute("DROP INDEX idx_jobs_dedup_softkey")
        await connection.commit()
        with pytest.raises(ValueError, match="schema v1 is inconsistent"):
            await ensure_sqlite_schema(connection)

    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute("DELETE FROM run_leases WHERE kind='evaluate'")
        await connection.commit()
        with pytest.raises(ValueError, match="run lease rows differ"):
            await ensure_sqlite_schema(connection)


@pytest.mark.asyncio
async def test_migration_failure_rolls_back_every_ddl_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected failure leaves user_version zero and no partial tables."""
    calls = 0
    original = sqlite_schema._execute_schema_statement

    async def fail_after_three(connection: aiosqlite.Connection, sql: str) -> None:
        nonlocal calls
        calls += 1
        if calls == _INJECTED_FAILURE_CALL:
            raise RuntimeError("injected schema failure")
        await original(connection, sql)

    monkeypatch.setattr(sqlite_schema, "_execute_schema_statement", fail_after_three)
    async with aiosqlite.connect(":memory:") as connection:
        with pytest.raises(RuntimeError, match="injected schema failure"):
            await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == 0
        assert await _table_names(connection) == ()
