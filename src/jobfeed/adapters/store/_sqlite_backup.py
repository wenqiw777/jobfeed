"""Stage, validate, and publish SQLite backups without partial files."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiosqlite

from jobfeed.adapters.store._sqlite_connection import (
    _open_read_only_connection,
    _scalar,
)
from jobfeed.adapters.store._sqlite_errors import SqliteDatabaseValidationError


async def _create_online_backup(
    source: aiosqlite.Connection,
    destination: Path,
) -> Path:
    stage = _create_stage(destination, "backup")
    try:
        await _copy_database(source, stage)
        await _validate_database(stage)
        _sync_file(stage)
        os.link(stage, destination)
        stage.unlink()
        _sync_directory(destination.parent)
    except BaseException:
        _remove_owned_stage(stage)
        raise
    return destination


async def _restore_database(
    source: Path,
    target: Path,
    validate_stage: Callable[[Path], Awaitable[None]],
) -> None:
    stage = _create_stage(target, "restore")
    try:
        source_connection = await _open_validated(source)
        try:
            await _copy_database(source_connection, stage)
        finally:
            await source_connection.close()
        await _validate_database(stage)
        await validate_stage(stage)
        await _validate_database(stage)
        _assert_no_live_sidecars(stage)
        _sync_file(stage)
        _assert_no_live_sidecars(target)
        os.replace(stage, target)
        _sync_directory(target.parent)
    except BaseException:
        _remove_owned_stage(stage)
        raise


async def _validate_database(path: Path) -> None:
    connection = await _open_validated(path)
    await connection.close()


async def _open_validated(path: Path) -> aiosqlite.Connection:
    try:
        connection = await _open_read_only_connection(path)
        try:
            integrity = await _scalar(connection, "PRAGMA integrity_check")
            foreign_key_cursor = await connection.execute("PRAGMA foreign_key_check")
            try:
                foreign_key_violation = await foreign_key_cursor.fetchone()
            finally:
                await foreign_key_cursor.close()
        except BaseException:
            await connection.close()
            raise
    except aiosqlite.DatabaseError as error:
        msg = f"SQLite database validation failed for {path}"
        raise SqliteDatabaseValidationError(msg) from error
    if integrity != "ok" or foreign_key_violation is not None:
        await connection.close()
        msg = f"SQLite integrity or foreign-key validation failed for {path}"
        raise SqliteDatabaseValidationError(msg)
    return connection


async def _copy_database(
    source: aiosqlite.Connection,
    destination: Path,
) -> None:
    target = await aiosqlite.connect(destination, isolation_level=None)
    try:
        await source.backup(target)
    finally:
        await target.close()


def _create_stage(target: Path, operation: str) -> Path:
    file_descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.{operation}-",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(file_descriptor)
    return Path(raw_path)


def _assert_no_live_sidecars(target: Path) -> None:
    sidecars = [Path(f"{target}-wal"), Path(f"{target}-shm")]
    if any(path.exists() for path in sidecars):
        msg = "target has live SQLite WAL sidecars and cannot be replaced safely"
        raise SqliteDatabaseValidationError(msg)


def _sync_file(path: Path) -> None:
    file_descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _sync_directory(path: Path) -> None:
    file_descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _remove_owned_stage(path: Path) -> None:
    for owned_path in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        with contextlib.suppress(FileNotFoundError):
            owned_path.unlink()
