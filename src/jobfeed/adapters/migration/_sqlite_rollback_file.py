"""Open and bind a rollback snapshot to one immutable SQLite file identity."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import aiosqlite

from jobfeed.adapters.migration._sqlite_rollback_types import (
    SqliteRollbackSourceIdentity,
)

_HASH_CHUNK_SIZE = 1024 * 1024


async def open_immutable_source(
    path: Path,
) -> tuple[aiosqlite.Connection, SqliteRollbackSourceIdentity]:
    """Open a path read-only and reject WAL-backed or path-swapped content.

    Args:
        path: Closed SQLite source file with no active WAL sidecar.

    Returns:
        Read-only connection and the verified path-free file identity.

    Raises:
        ValueError: If the source is missing, unsafe, empty, or changes while read.
        OSError: If opening or statting the file fails.
    """
    resolved = path.resolve(strict=True)
    before = resolved.stat()
    if not resolved.is_file() or before.st_size <= 0:
        raise ValueError("rollback source must be a non-empty regular file")
    _reject_wal(resolved)
    connection = await _read_only_connection(resolved)
    try:
        journal_mode = str(await _scalar(connection, "PRAGMA journal_mode")).lower()
        if journal_mode == "wal":
            raise ValueError("rollback source WAL mode is unsafe; checkpoint first")
        await connection.execute("BEGIN")
        await _scalar(connection, "SELECT COUNT(*) FROM sqlite_schema")
        digest = _file_sha256(resolved)
        after = resolved.stat()
        opened = await _opened_identity(connection)
        if not _same_file(before, after) or opened != (after.st_dev, after.st_ino):
            raise ValueError("rollback source file identity changed during open")
        _reject_wal(resolved)
        return connection, SqliteRollbackSourceIdentity(
            file_size_bytes=after.st_size,
            file_sha256=digest,
            device=after.st_dev,
            inode=after.st_ino,
            journal_mode=journal_mode,
            has_wal=False,
        )
    except BaseException:
        await connection.close()
        raise


def assert_source_identity(path: Path, source: SqliteRollbackSourceIdentity) -> None:
    """Require the source path still names the originally opened file.

    Args:
        path: Original rollback source path.
        source: Physical identity captured at snapshot open.

    Raises:
        ValueError: If the path was removed, replaced, or gained an active WAL.
    """
    try:
        stat = path.resolve(strict=True).stat()
    except OSError as exc:
        raise ValueError(
            "rollback source file identity is no longer available"
        ) from exc
    if (stat.st_dev, stat.st_ino) != (source.device, source.inode):
        raise ValueError("rollback source file identity changed")
    _reject_wal(path)


def assert_source_bytes(path: Path, source: SqliteRollbackSourceIdentity) -> None:
    """Require the closed source bytes still match captured evidence.

    Args:
        path: Original rollback source path after the read snapshot closes.
        source: Physical identity and digest captured at snapshot open.

    Raises:
        ValueError: If metadata, WAL safety, or file bytes changed.
    """
    assert_source_identity(path, source)
    stat = path.stat()
    if stat.st_size != source.file_size_bytes:
        raise ValueError("rollback source file size changed")
    if _file_sha256(path) != source.file_sha256:
        raise ValueError("rollback source file bytes changed")


async def _read_only_connection(path: Path) -> aiosqlite.Connection:
    uri = f"{path.as_uri()}?mode=ro&immutable=1"
    connection = await aiosqlite.connect(uri, uri=True, isolation_level=None)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA query_only=ON")
    return connection


async def _opened_identity(connection: aiosqlite.Connection) -> tuple[int, int]:
    cursor = await connection.execute("PRAGMA database_list")
    try:
        rows = list(await cursor.fetchall())
    finally:
        await cursor.close()
    if len(rows) != 1:
        raise ValueError("rollback source database_list is not singular")
    opened = Path(str(rows[0][2])).resolve(strict=True).stat()
    return opened.st_dev, opened.st_ino


def _reject_wal(path: Path) -> None:
    wal = Path(f"{path}-wal")
    if os.path.lexists(wal) and wal.stat().st_size > 0:
        raise ValueError("rollback source has an active WAL; checkpoint first")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


async def _scalar(connection: aiosqlite.Connection, sql: str) -> object:
    cursor = await connection.execute(sql)
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise ValueError(f"rollback source query returned no row: {sql}")
    return row[0]
