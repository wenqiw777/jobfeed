"""All-table canonical row replay for PostgreSQL rollback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from jobfeed.adapters.migration._canonical_codec_v1 import _timestamp
from jobfeed.adapters.migration._pg_rollback_types import CanonicalRollbackSource
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    CanonicalManifestColumn,
)

FaultHook = Callable[[str], Awaitable[None]]


async def _replay_all(
    connection: Any,
    source: CanonicalRollbackSource,
    *,
    chunk_size: int,
    fault_hook: FaultHook,
) -> tuple[dict[str, int], dict[str, int]]:
    """Reconcile 14 tables. Time complexity is O(source + target rows)."""
    replayed: dict[str, int] = {}
    deleted: dict[str, int] = {}
    for position, table in enumerate(CANONICAL_SCHEMA_MANIFEST_V1.tables):
        count = await _upsert_table(
            connection, source, table.name, chunk_size=chunk_size
        )
        replayed[table.name] = count
        if table.name == "jobs":
            await fault_hook("after_jobs")
        if position == len(CANONICAL_SCHEMA_MANIFEST_V1.tables) // 2:
            await fault_hook("mid_replay")
    for table in reversed(CANONICAL_SCHEMA_MANIFEST_V1.tables):
        result = await connection.execute(
            f'DELETE FROM "{table.name}" AS target WHERE NOT EXISTS '
            f'(SELECT 1 FROM "_rollback_keys_{table.name}" source WHERE '
            f"{_key_match(table.name, 'source', 'target')})"
        )
        deleted[table.name] = int(str(result).split()[-1])
    return replayed, {name: count for name, count in deleted.items() if count}


async def _upsert_table(
    connection: Any,
    source: CanonicalRollbackSource,
    table_name: str,
    *,
    chunk_size: int,
) -> int:
    table = next(
        item for item in CANONICAL_SCHEMA_MANIFEST_V1.tables if item.name == table_name
    )
    key_columns = ",".join(f'"{name}"' for name in table.primary_key)
    await connection.execute(
        f'CREATE TEMP TABLE "_rollback_keys_{table.name}" '
        f'ON COMMIT DROP AS SELECT {key_columns} FROM "{table.name}" WITH NO DATA'
    )
    names = tuple(column.name for column in table.columns)
    insert_columns = ",".join(f'"{name}"' for name in names)
    placeholders = ",".join(f"${index}" for index in range(1, len(names) + 1))
    updates = ",".join(
        f'"{name}"=EXCLUDED."{name}"' for name in names if name not in table.primary_key
    )
    conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    statement = (
        f'INSERT INTO "{table.name}"({insert_columns}) VALUES({placeholders}) '
        f"ON CONFLICT ({key_columns}) {conflict}"
    )
    key_placeholders = ",".join(
        f"${index}" for index in range(1, len(table.primary_key) + 1)
    )
    key_statement = (
        f'INSERT INTO "_rollback_keys_{table.name}"({key_columns}) '
        f"VALUES({key_placeholders})"
    )
    count = 0
    async for row in source.stream_table(table.name, chunk_size=chunk_size):
        await connection.execute(
            statement,
            *(_postgres_value(column, row[column.name]) for column in table.columns),
        )
        await connection.execute(
            key_statement, *(row[name] for name in table.primary_key)
        )
        count += 1
    return count


def _key_match(table_name: str, source: str, target: str) -> str:
    table = next(
        item for item in CANONICAL_SCHEMA_MANIFEST_V1.tables if item.name == table_name
    )
    return " AND ".join(
        f'{source}."{name}"={target}."{name}"' for name in table.primary_key
    )


def _postgres_value(column: CanonicalManifestColumn, value: object) -> object:
    if value is None:
        return None
    if column.codec_kind == "timestamp":
        return _timestamp(value)
    if column.source_sql_type == "boolean" and type(value) is int:
        return bool(value)
    return value
