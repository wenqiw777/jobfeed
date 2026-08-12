"""Versioned, atomic SQLite schema creation for the runtime store adapter."""

from __future__ import annotations

import re
from typing import Final

import aiosqlite

from jobfeed.adapters.store._sqlite_schema_metadata import (
    SQLITE_METADATA,
    SQLITE_SCHEMA_VERSION,
    SQLITE_TABLE_NAMES,
    schema_ddl_statements,
)

_SEED_LEASE_SQL: Final = (
    "INSERT INTO run_leases(kind, generation) VALUES('scan', 0),('evaluate', 0)"
)
_CREATE_PREFIX = re.compile(r"^CREATE\s+(TABLE|INDEX|TRIGGER)\s+([^\s(]+)", re.I)


async def ensure_sqlite_schema(connection: aiosqlite.Connection) -> None:
    """Create or validate the exact SQLite schema version supported by Jobfeed.

    Args:
        connection: Open aiosqlite connection with no active transaction.

    Raises:
        RuntimeError: If called inside an existing transaction.
        ValueError: If the version is unknown or the live schema is inconsistent.
        aiosqlite.Error: If transactional schema creation fails.
    """
    if connection.in_transaction:
        raise RuntimeError("SQLite schema migration requires no active transaction")
    version = await _schema_version(connection)
    if version == 0:
        await _migrate_zero_to_one(connection)
        return
    if version == SQLITE_SCHEMA_VERSION:
        await _validate_v1(connection)
        return
    raise ValueError(f"unsupported SQLite schema version: {version}")


async def _migrate_zero_to_one(connection: aiosqlite.Connection) -> None:
    await connection.execute("BEGIN IMMEDIATE")
    try:
        version = await _schema_version(connection)
        if version == SQLITE_SCHEMA_VERSION:
            await _validate_v1(connection)
            await connection.commit()
            return
        if version != 0:
            raise ValueError(f"unsupported SQLite schema version: {version}")
        if await _user_objects(connection):
            raise ValueError("SQLite version 0 database is not empty")
        for statement in schema_ddl_statements():
            await _execute_schema_statement(connection, statement)
        await connection.execute(_SEED_LEASE_SQL)
        await connection.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION}")
        await _validate_v1(connection)
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


async def _schema_version(connection: aiosqlite.Connection) -> int:
    version = await _scalar(connection, "PRAGMA user_version")
    if type(version) is not int:
        raise ValueError("SQLite user_version is not an integer")
    return version


async def _execute_schema_statement(connection: aiosqlite.Connection, sql: str) -> None:
    await connection.execute(sql)


async def _validate_v1(connection: aiosqlite.Connection) -> None:
    expected = _expected_schema_objects()
    live = await _live_schema_objects(connection)
    if live != expected:
        missing = sorted(set(expected) - set(live))
        extra = sorted(set(live) - set(expected))
        changed = sorted(
            name for name in set(live) & set(expected) if live[name] != expected[name]
        )
        raise ValueError(
            "SQLite schema v1 is inconsistent: "
            f"missing={missing}, extra={extra}, changed={changed}"
        )
    cursor = await connection.execute(
        "SELECT kind, generation, owner_id, run_id, heartbeat_at, expires_at "
        "FROM run_leases ORDER BY kind"
    )
    leases = await cursor.fetchall()
    await cursor.close()
    if [row[0] for row in leases] != ["evaluate", "scan"]:
        raise ValueError("SQLite schema v1 is inconsistent: run lease rows differ")


def _expected_schema_objects() -> dict[tuple[str, str], str]:
    objects: dict[tuple[str, str], str] = {}
    for statement in schema_ddl_statements():
        match = _CREATE_PREFIX.match(statement.lstrip())
        if match is None:
            raise RuntimeError("generated SQLite DDL has an unknown object")
        kind, raw_name = match.groups()
        name = raw_name.strip('"`[]')
        objects[(kind.lower(), name)] = _normalize_sql(statement)
    return objects


async def _live_schema_objects(
    connection: aiosqlite.Connection,
) -> dict[tuple[str, str], str]:
    cursor = await connection.execute(
        "SELECT type, name, sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return {
        (str(kind), str(name)): _normalize_sql(str(sql)) for kind, name, sql in rows
    }


async def _user_objects(connection: aiosqlite.Connection) -> tuple[str, ...]:
    cursor = await connection.execute(
        "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return tuple(str(row[0]) for row in rows)


async def _scalar(connection: aiosqlite.Connection, sql: str) -> object:
    cursor = await connection.execute(sql)
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise ValueError("SQLite schema query returned no row")
    return row[0]


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split()).rstrip(";")


__all__ = [
    "SQLITE_METADATA",
    "SQLITE_SCHEMA_VERSION",
    "SQLITE_TABLE_NAMES",
    "ensure_sqlite_schema",
]
