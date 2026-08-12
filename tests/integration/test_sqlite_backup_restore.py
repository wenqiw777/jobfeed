"""Integration contracts for safe SQLite online backup and restore."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store import _sqlite_backup
from jobfeed.adapters.store.sqlite_lifecycle import (
    SqliteDatabaseValidationError,
    SqliteLifecycle,
    SqliteLifecycleBusyError,
    SqliteLifecycleStateError,
)


async def _initialize_records(connection: aiosqlite.Connection) -> None:
    await connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS children (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES parents(id)
        );
        """
    )


async def _insert_parent(lifecycle: SqliteLifecycle, name: str) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute("INSERT INTO parents(name) VALUES (?)", (name,))


async def _parent_names(lifecycle: SqliteLifecycle) -> list[str]:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute("SELECT name FROM parents ORDER BY id")
        rows = await cursor.fetchall()
        await cursor.close()
    return [str(row[0]) for row in rows]


async def test_online_backup_captures_committed_wal_data(tmp_path: Path) -> None:
    """Backup uses SQLite's online snapshot while the source remains open."""
    database = tmp_path / "jobfeed.db"
    backup = tmp_path / "jobfeed.backup.db"
    lifecycle = SqliteLifecycle(database, _initialize_records)
    await lifecycle.open()
    await _insert_parent(lifecycle, "new-in-wal")

    result = await lifecycle.backup_to(backup)

    assert result == backup
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT name FROM parents").fetchall() == [
            ("new-in-wal",)
        ]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    await lifecycle.close()


async def test_backup_never_overwrites_existing_path_or_symlink(
    tmp_path: Path,
) -> None:
    """Exclusive publication preserves any pre-existing destination object."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", _initialize_records)
    await lifecycle.open()
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"keep-me")

    with pytest.raises(FileExistsError):
        await lifecycle.backup_to(destination)
    assert destination.read_bytes() == b"keep-me"

    target = tmp_path / "symlink-target"
    target.write_bytes(b"also-keep")
    symlink = tmp_path / "existing-link.db"
    symlink.symlink_to(target)
    with pytest.raises(FileExistsError):
        await lifecycle.backup_to(symlink)
    assert symlink.is_symlink()
    assert target.read_bytes() == b"also-keep"
    await lifecycle.close()


async def test_backup_failure_removes_owned_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted backup leaves no output or run-owned staging file."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", _initialize_records)
    await lifecycle.open()
    destination = tmp_path / "failed.db"

    async def fail_copy(
        _source: aiosqlite.Connection,
        _destination: Path,
    ) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_sqlite_backup, "_copy_database", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        await lifecycle.backup_to(destination)

    assert not destination.exists()
    assert list(tmp_path.glob(".failed.db.backup-*.tmp")) == []
    await lifecycle.close()


async def test_restore_validated_backup_atomically_replaces_closed_database(
    tmp_path: Path,
) -> None:
    """A valid backup replaces the closed target and remains reusable."""
    source = SqliteLifecycle(tmp_path / "source.db", _initialize_records)
    await source.open()
    await _insert_parent(source, "restored")
    backup = await source.backup_to(tmp_path / "source.backup.db")
    backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    await source.close()

    target = SqliteLifecycle(tmp_path / "target.db", _initialize_records)
    await target.open()
    await _insert_parent(target, "old")
    await target.close()

    await target.restore_from(backup)

    assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_digest
    assert not target.is_open
    await target.open()
    assert await _parent_names(target) == ["restored"]
    await target.close()


async def test_restore_rejects_open_or_locked_target(tmp_path: Path) -> None:
    """Restore cannot race an open lifecycle in this or another instance."""
    source = SqliteLifecycle(tmp_path / "source.db", _initialize_records)
    await source.open()
    backup = await source.backup_to(tmp_path / "source.backup.db")
    await source.close()

    first = SqliteLifecycle(tmp_path / "target.db", _initialize_records)
    await first.open()
    with pytest.raises(SqliteLifecycleStateError, match="must be closed"):
        await first.restore_from(backup)

    second = SqliteLifecycle(tmp_path / "target.db", _initialize_records)
    with pytest.raises(SqliteLifecycleBusyError, match="exclusive"):
        await second.restore_from(backup)
    await first.close()


@pytest.mark.parametrize("invalid_kind", ["corrupt", "foreign_key"])
async def test_restore_validation_failure_preserves_target_and_cleans_stage(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    """Corrupt or FK-invalid sources cannot replace the current database."""
    target_path = tmp_path / "target.db"
    target = SqliteLifecycle(target_path, _initialize_records)
    await target.open()
    await _insert_parent(target, "keep")
    await target.close()
    before = target_path.read_bytes()

    source_path = tmp_path / f"{invalid_kind}.db"
    if invalid_kind == "corrupt":
        source_path.write_bytes(b"not a sqlite database")
    else:
        _write_foreign_key_invalid_database(source_path)

    with pytest.raises(SqliteDatabaseValidationError):
        await target.restore_from(source_path)

    assert target_path.read_bytes() == before
    assert list(tmp_path.glob(".target.db.restore-*.tmp")) == []


async def test_restore_replace_failure_preserves_target_and_cleans_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed atomic replace preserves the old target and removes staging."""
    source = SqliteLifecycle(tmp_path / "source.db", _initialize_records)
    await source.open()
    await _insert_parent(source, "replacement")
    backup = await source.backup_to(tmp_path / "source.backup.db")
    await source.close()

    target_path = tmp_path / "target.db"
    target = SqliteLifecycle(target_path, _initialize_records)
    await target.open()
    await _insert_parent(target, "keep")
    await target.close()
    before = target_path.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(_sqlite_backup.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        await target.restore_from(backup)

    assert target_path.read_bytes() == before
    assert list(tmp_path.glob(".target.db.restore-*.tmp")) == []


def _write_foreign_key_invalid_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE parents (id INTEGER PRIMARY KEY);
            CREATE TABLE children (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parents(id)
            );
            INSERT INTO children(id, parent_id) VALUES (1, 999);
            """
        )
