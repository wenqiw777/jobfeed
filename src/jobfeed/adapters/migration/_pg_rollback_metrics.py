"""Canonical PostgreSQL table metrics inside the rollback transaction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jobfeed.adapters.migration.canonical_row import CanonicalRowHasher
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
)


async def _read_table_metrics(
    connection: Any, *, chunk_size: int
) -> dict[str, Mapping[str, object]]:
    """Hash all target rows. Time complexity is O(rows * columns)."""
    metrics: dict[str, Mapping[str, object]] = {}
    for table, schema in zip(
        CANONICAL_SCHEMA_MANIFEST_V1.tables,
        CANONICAL_ROW_SCHEMAS_V1,
        strict=True,
    ):
        hasher = CanonicalRowHasher(schema)
        count = 0
        async for record in connection.cursor(
            _table_query(table.name), prefetch=chunk_size
        ):
            hasher.update_rows([dict(record)])
            count += 1
        maximum = None
        if any(column.name == "id" for column in table.columns):
            maximum = await connection.fetchval(f'SELECT MAX(id) FROM "{table.name}"')
        metrics[table.name] = {
            "row_count": count,
            "primary_key": list(table.primary_key),
            "max_identity": maximum,
            "canonical_sha256": hasher.hexdigest(),
        }
    return metrics


def _table_query(table_name: str) -> str:
    table = next(
        item for item in CANONICAL_SCHEMA_MANIFEST_V1.tables if item.name == table_name
    )
    projections = ",".join(
        (
            f'"{column.name}"::text AS "{column.name}"'
            if column.source_sql_type == "jsonb"
            else f'"{column.name}"'
        )
        for column in table.columns
    )
    by_name = {column.name: column for column in table.columns}
    order = ",".join(
        (f'"{name}" COLLATE "C"' if by_name[name].codec_kind == "text" else f'"{name}"')
        for name in table.primary_key
    )
    return f'SELECT {projections} FROM "{table.name}" ORDER BY {order}'
