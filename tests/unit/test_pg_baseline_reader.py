"""PostgreSQL baseline reader behavior over server-side cursors."""

from __future__ import annotations

from collections.abc import Iterator

from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)


class _ServerCursor:
    """Model psycopg2 named cursors whose description is initially absent."""

    description = None
    itersize = 0

    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def execute(self, sql: str) -> None:
        """Accept the generated trusted query."""
        assert sql.startswith("SELECT ")

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        """Yield one full registry-shaped row."""
        yield self._row

    def close(self) -> None:
        """Close the fake cursor."""


class _Connection:
    def __init__(self, rows: dict[str, tuple[object, ...]]) -> None:
        self._rows = rows

    def cursor(self, name: str) -> _ServerCursor:
        """Return the named cursor for its exact registry table."""
        table = name.removeprefix("baseline_")
        return _ServerCursor(self._rows[table])


def test_stream_table_uses_frozen_columns_when_named_cursor_description_is_none() -> (
    None
):
    """All 14 real schemas retain full row shape before the first server fetch."""
    rows = {
        table.name: tuple(
            f"{table.name}-{index}" for index, _ in enumerate(table.columns)
        )
        for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
    }
    reader = PostgresBaselineReader("unused")
    reader._connection = _Connection(rows)  # type: ignore[assignment]

    for table in CANONICAL_SCHEMA_MANIFEST_V1.tables:
        assert list(reader.stream_table(table.name, chunk_size=100)) == [
            dict(
                zip(
                    (column.name for column in table.columns),
                    rows[table.name],
                    strict=True,
                )
            )
        ]
