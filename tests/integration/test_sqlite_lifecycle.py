"""Integration contracts for SQLite connection lifecycle behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store import _sqlite_connection
from jobfeed.adapters.store.sqlite_lifecycle import (
    SqliteLifecycle,
    SqliteLifecycleStateError,
    UnsupportedSqliteVersionError,
)

_BUSY_TIMEOUT_MS = 5_000


async def _initialize_labels(connection: aiosqlite.Connection) -> None:
    await connection.execute("CREATE TABLE IF NOT EXISTS labels (value TEXT NOT NULL)")
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS labels_fold ON labels(unicode_casefold(value))"
    )


async def test_open_close_are_idempotent_and_initializer_runs_once(
    tmp_path: Path,
) -> None:
    """Open configures one lifecycle and repeated close remains safe."""
    calls = 0

    async def initialize(connection: aiosqlite.Connection) -> None:
        nonlocal calls
        calls += 1
        await _initialize_labels(connection)

    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", initialize)

    await lifecycle.open()
    await lifecycle.open()
    assert lifecycle.is_open
    assert calls == 1

    await lifecycle.close()
    await lifecycle.close()
    assert not lifecycle.is_open


async def test_open_creates_missing_database_parent(tmp_path: Path) -> None:
    """A fresh checkout can open the default database before data exists."""
    database = tmp_path / "missing" / "nested" / "jobfeed.db"
    lifecycle = SqliteLifecycle(database, _initialize_labels)

    await lifecycle.open()

    assert database.is_file()
    await lifecycle.close()


async def test_every_connection_has_required_pragmas_and_unicode_casefold(
    tmp_path: Path,
) -> None:
    """Each leased connection gets WAL, FK, timeout, and deterministic folding."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", _initialize_labels)
    await lifecycle.open()

    for _ in range(2):
        async with lifecycle.connection() as connection:
            journal_mode = await _scalar(connection, "PRAGMA journal_mode")
            foreign_keys = await _scalar(connection, "PRAGMA foreign_keys")
            busy_timeout = await _scalar(connection, "PRAGMA busy_timeout")
            folded = await _scalar(
                connection,
                "SELECT unicode_casefold('Straße')",
            )

            assert str(journal_mode).lower() == "wal"
            assert foreign_keys == 1
            assert busy_timeout == _BUSY_TIMEOUT_MS
            assert folded == "strasse"

    await lifecycle.close()


async def test_unsupported_sqlite_version_fails_before_initializer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime below the fixed version floor never initializes a schema."""
    was_initialized = False

    async def initialize(_connection: aiosqlite.Connection) -> None:
        nonlocal was_initialized
        was_initialized = True

    monkeypatch.setattr(
        _sqlite_connection,
        "_MINIMUM_SQLITE_VERSION",
        (99, 0, 0),
    )
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", initialize)

    with pytest.raises(UnsupportedSqliteVersionError, match=r"99\.0\.0"):
        await lifecycle.open()

    assert not lifecycle.is_open
    assert not was_initialized
    with pytest.raises(SqliteLifecycleStateError, match="not open"):
        async with lifecycle.connection():
            pass


async def test_initializer_failure_leaves_lifecycle_closed(tmp_path: Path) -> None:
    """Failed schema initialization never publishes a usable lifecycle."""

    async def fail(_connection: aiosqlite.Connection) -> None:
        raise RuntimeError("schema failed")

    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", fail)

    with pytest.raises(RuntimeError, match="schema failed"):
        await lifecycle.open()

    assert not lifecycle.is_open
    with pytest.raises(SqliteLifecycleStateError, match="not open"):
        async with lifecycle.connection():
            pass


async def test_initializer_must_finish_its_transaction(tmp_path: Path) -> None:
    """An initializer cannot leak its migration transaction into runtime use."""

    async def leave_transaction_open(connection: aiosqlite.Connection) -> None:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute("CREATE TABLE partial_schema (id INTEGER)")

    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", leave_transaction_open)

    with pytest.raises(SqliteLifecycleStateError, match="active transaction"):
        await lifecycle.open()

    assert not lifecycle.is_open
    with sqlite3.connect(tmp_path / "jobfeed.db") as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial_schema'"
        ).fetchall()
    assert tables == []


async def test_close_refuses_active_leased_connection(tmp_path: Path) -> None:
    """Close cannot release the restore lock while a connection is still leased."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", _initialize_labels)
    await lifecycle.open()

    async with lifecycle.connection():
        with pytest.raises(SqliteLifecycleStateError, match="active connection"):
            await lifecycle.close()
        assert lifecycle.is_open

    await lifecycle.close()


async def _scalar(connection: aiosqlite.Connection, statement: str) -> object:
    cursor = await connection.execute(statement)
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    return row[0]
