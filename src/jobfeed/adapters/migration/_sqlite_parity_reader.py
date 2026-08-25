"""Read canonical migrated tables from the current SQLite schema."""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from jobfeed.adapters.migration.canonical_row import CanonicalRowHasher
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
)
from jobfeed.adapters.store._sqlite_schema_metadata import SQLITE_SCHEMA_VERSION
from jobfeed.adapters.store.sqlite_schema import _validate_v2

_GENERATED_ID_TABLES = frozenset(
    {
        "jobs",
        "evaluations",
        "pipeline_runs",
        "job_status_history",
        "llm_usage",
        "interview_rounds",
        "step_timings",
    }
)
_EXPECTED_LEASES = (
    ("evaluate", 0, None, None, None, None),
    ("scan", 0, None, None, None, None),
)


@dataclass(frozen=True, kw_only=True)
class SqliteTableMetric:
    """One canonical table count, identity maximum, and ordered digest."""

    table_name: str
    row_count: int
    max_identity: int | None
    canonical_sha256: str


async def validate_sqlite_v1(connection: aiosqlite.Connection) -> int:
    """Validate current DDL and the two idle migration lease seeds.

    Args:
        connection: Open target connection inside a read transaction.

    Returns:
        The validated current SQLite schema version.

    Raises:
        ValueError: If version, DDL, tables, or lease seeds differ.
    """
    version = await _scalar(connection, "PRAGMA user_version")
    if version != SQLITE_SCHEMA_VERSION:
        raise ValueError(
            "SQLite parity requires schema version "
            f"{SQLITE_SCHEMA_VERSION}, got {version!r}"
        )
    await _validate_v2(connection)
    leases = await _rows(
        connection,
        "SELECT kind, generation, owner_id, run_id, heartbeat_at, expires_at "
        "FROM run_leases ORDER BY kind",
    )
    if tuple(tuple(row) for row in leases) != _EXPECTED_LEASES:
        raise ValueError("SQLite parity requires exact idle run lease seed rows")
    return SQLITE_SCHEMA_VERSION


async def validate_sqlite_integrity(connection: aiosqlite.Connection) -> None:
    """Require SQLite structural and foreign-key integrity.

    Args:
        connection: Open target connection inside a read transaction.

    Raises:
        ValueError: If either integrity check reports a problem.
    """
    integrity = await _rows(connection, "PRAGMA integrity_check")
    if tuple(tuple(row) for row in integrity) != (("ok",),):
        raise ValueError(f"SQLite integrity_check failed: {integrity!r}")


async def sqlite_foreign_key_failures(
    connection: aiosqlite.Connection,
) -> tuple[tuple[object, ...], ...]:
    """Return every live SQLite foreign-key violation.

    Args:
        connection: Open target connection inside a read transaction.

    Returns:
        Empty tuple when valid, otherwise all failure rows.
    """
    rows = await _rows(connection, "PRAGMA foreign_key_check")
    return tuple(tuple(row) for row in rows)


async def read_sqlite_table_metrics(
    connection: aiosqlite.Connection, *, chunk_size: int
) -> tuple[SqliteTableMetric, ...]:
    """Stream all 14 tables in canonical primary-key order.

    Args:
        connection: Open target connection inside a read transaction.
        chunk_size: Positive maximum rows fetched into memory per table chunk.

    Returns:
        Exact registry-ordered table metrics.

    Raises:
        ValueError: If a row cannot satisfy the canonical v1 codec.

    Complexity:
        O(R) time and O(chunk_size) row memory across all migrated rows.
    """
    metrics = []
    for table, schema in zip(
        CANONICAL_SCHEMA_MANIFEST_V1.tables,
        CANONICAL_ROW_SCHEMAS_V1,
        strict=True,
    ):
        hasher = CanonicalRowHasher(schema)
        row_count = 0
        cursor = await connection.execute(_table_query(table.name))
        try:
            while rows := list(await cursor.fetchmany(chunk_size)):
                hasher.update_rows(dict(row) for row in rows)
                row_count += len(rows)
        finally:
            await cursor.close()
        maximum = None
        if table.name in _GENERATED_ID_TABLES:
            maximum = _optional_int(
                await _scalar(connection, f'SELECT MAX(id) FROM "{table.name}"')
            )
        metrics.append(
            SqliteTableMetric(
                table_name=table.name,
                row_count=row_count,
                max_identity=maximum,
                canonical_sha256=hasher.hexdigest(),
            )
        )
    return tuple(metrics)


def _table_query(table_name: str) -> str:
    table = next(
        item for item in CANONICAL_SCHEMA_MANIFEST_V1.tables if item.name == table_name
    )
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    by_name = {column.name: column for column in table.columns}
    order = ", ".join(
        (
            f'CAST("{name}" AS BLOB)'
            if by_name[name].codec_kind == "text"
            else f'"{name}"'
        )
        for name in table.primary_key
    )
    return f'SELECT {columns} FROM "{table.name}" ORDER BY {order}'


async def _rows(connection: aiosqlite.Connection, sql: str) -> list[aiosqlite.Row]:
    cursor = await connection.execute(sql)
    try:
        return list(await cursor.fetchall())
    finally:
        await cursor.close()


async def _scalar(connection: aiosqlite.Connection, sql: str) -> object:
    rows = await _rows(connection, sql)
    if len(rows) != 1:
        raise ValueError(f"SQLite parity scalar returned {len(rows)} rows")
    return rows[0][0]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"SQLite identity maximum is not an integer: {value!r}")
    return value
