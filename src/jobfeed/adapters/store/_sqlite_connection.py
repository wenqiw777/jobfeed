"""Configure every SQLite adapter connection with required invariants."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from jobfeed.adapters.store._sqlite_errors import UnsupportedSqliteVersionError

_MINIMUM_SQLITE_VERSION = (3, 35, 0)
_BUSY_TIMEOUT_MS = 5_000


async def _open_configured_connection(path: Path) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(path, isolation_level=None)
    try:
        await _configure_connection(connection)
    except BaseException:
        await connection.close()
        raise
    return connection


async def _open_read_only_connection(path: Path) -> aiosqlite.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = await aiosqlite.connect(uri, uri=True, isolation_level=None)
    try:
        await _register_connection_features(connection)
    except BaseException:
        await connection.close()
        raise
    return connection


async def _configure_connection(connection: aiosqlite.Connection) -> None:
    await _register_connection_features(connection)
    await connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    await connection.execute("PRAGMA foreign_keys=ON")
    journal_mode = await _scalar(connection, "PRAGMA journal_mode=WAL")
    foreign_keys = await _scalar(connection, "PRAGMA foreign_keys")
    busy_timeout = await _scalar(connection, "PRAGMA busy_timeout")
    if str(journal_mode).lower() != "wal":
        msg = f"SQLite refused WAL mode for this connection: {journal_mode!r}"
        raise RuntimeError(msg)
    if foreign_keys != 1 or busy_timeout != _BUSY_TIMEOUT_MS:
        msg = "SQLite refused required foreign_keys or busy_timeout settings"
        raise RuntimeError(msg)


async def _scalar(connection: aiosqlite.Connection, statement: str) -> object:
    cursor = await connection.execute(statement)
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        msg = f"SQLite returned no result for {statement!r}"
        raise RuntimeError(msg)
    return row[0]


async def _register_connection_features(connection: aiosqlite.Connection) -> None:
    raw_version = await _scalar(connection, "SELECT sqlite_version()")
    version = _parse_version(raw_version)
    if version < _MINIMUM_SQLITE_VERSION:
        required = ".".join(str(part) for part in _MINIMUM_SQLITE_VERSION)
        actual = ".".join(str(part) for part in version)
        msg = f"SQLite {required} or newer is required; found {actual}"
        raise UnsupportedSqliteVersionError(msg)
    await connection.create_function(
        "unicode_casefold",
        1,
        _unicode_casefold,
        deterministic=True,
    )


def _parse_version(raw_version: object) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in str(raw_version).split("."))
    except ValueError as error:
        msg = f"SQLite returned an invalid version: {raw_version!r}"
        raise UnsupportedSqliteVersionError(msg) from error
    if len(parts) < len(_MINIMUM_SQLITE_VERSION):
        msg = f"SQLite returned an invalid version: {raw_version!r}"
        raise UnsupportedSqliteVersionError(msg)
    return parts[0], parts[1], parts[2]


def _unicode_casefold(value: str | None) -> str | None:
    return None if value is None else value.casefold()
