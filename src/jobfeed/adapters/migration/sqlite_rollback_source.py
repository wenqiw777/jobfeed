"""Consistent read-only SQLite-v1 source snapshots for lossless rollback."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import aiosqlite

from jobfeed.adapters.migration._sqlite_parity_aggregates import (
    capture_sqlite_aggregate_manifest,
)
from jobfeed.adapters.migration._sqlite_parity_reader import (
    read_sqlite_table_metrics,
    sqlite_foreign_key_failures,
    validate_sqlite_integrity,
    validate_sqlite_v1,
)
from jobfeed.adapters.migration._sqlite_rollback_file import (
    assert_source_bytes,
    assert_source_identity,
    open_immutable_source,
)
from jobfeed.adapters.migration._sqlite_rollback_types import (
    ROLLBACK_MANIFEST_VERSION,
    SqliteRollbackAggregates,
    SqliteRollbackManifest,
    SqliteRollbackSourceIdentity,
    SqliteRollbackTableMetric,
    rollback_aggregates,
    rollback_table_metrics,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    MIGRATED_TABLE_ORDER_V1,
    canonical_schema_manifest_document,
)


class SqliteRollbackSourceError(ValueError):
    """Fail-closed source snapshot validation or lifecycle error."""


class SqliteRollbackSnapshot:
    """One open, immutable, consistent SQLite rollback source snapshot."""

    def __init__(self, path: Path, *, as_of_utc: datetime, chunk_size: int) -> None:
        """Configure a closed source snapshot for the path, cutoff, and chunk size."""
        self._path = path.resolve(strict=False)
        self._as_of_utc = _utc_text(as_of_utc)
        self._chunk_size = chunk_size
        self._connection: aiosqlite.Connection | None = None
        self._source: SqliteRollbackSourceIdentity | None = None
        self._manifest: SqliteRollbackManifest | None = None

    @property
    def schema_version(self) -> int:
        """Return the validated source schema version.

        Returns:
            Version one while the snapshot is open.

        Raises:
            SqliteRollbackSourceError: If the snapshot is not open.
        """
        return self.manifest.sqlite_schema_version

    @property
    def source(self) -> SqliteRollbackSourceIdentity:
        """Return path-free immutable physical source identity.

        Returns:
            Captured file size, digest, inode, and journal safety evidence.

        Raises:
            SqliteRollbackSourceError: If the snapshot is not open.
        """
        if self._source is None:
            raise SqliteRollbackSourceError("rollback source snapshot is closed")
        return self._source

    @property
    def manifest(self) -> SqliteRollbackManifest:
        """Return the complete typed rollback source manifest.

        Returns:
            Schema, source, table, and aggregate evidence.

        Raises:
            SqliteRollbackSourceError: If the snapshot is not open.
        """
        if self._manifest is None:
            raise SqliteRollbackSourceError("rollback source snapshot is closed")
        return self._manifest

    @property
    def table_metrics(self) -> tuple[SqliteRollbackTableMetric, ...]:
        """Return exact registry-ordered canonical table metrics.

        Returns:
            All 14 table counts, identity maxima, and hashes.
        """
        return self.manifest.tables

    @property
    def aggregate_manifest(self) -> SqliteRollbackAggregates:
        """Return business aggregate evidence at the shared cutoff.

        Returns:
            Counts and four canonical aggregate hashes.
        """
        return self.manifest.aggregates

    async def __aenter__(self) -> SqliteRollbackSnapshot:
        """Open and validate the immutable read snapshot.

        Returns:
            This active snapshot.

        Raises:
            SqliteRollbackSourceError: If any source safety gate fails.
        """
        if self._connection is not None:
            raise SqliteRollbackSourceError("rollback source snapshot is already open")
        try:
            connection, source = await open_immutable_source(self._path)
            self._connection = connection
            self._source = source
            self._manifest = await self._capture_manifest(connection, source)
            assert_source_identity(self._path, source)
            return self
        except (OSError, TypeError, ValueError, aiosqlite.Error) as exc:
            await self._close()
            raise SqliteRollbackSourceError(str(exc)) from exc

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close and rehash the source, surfacing unsafe drift on success."""
        source = self._source
        await self._close()
        if exc_type is None and source is not None:
            try:
                assert_source_bytes(self._path, source)
            except (OSError, ValueError) as error:
                raise SqliteRollbackSourceError(str(error)) from error

    async def stream_table(
        self, table_name: str, *, chunk_size: int | None = None
    ) -> AsyncIterator[dict[str, object]]:
        """Stream one allowlisted table in canonical primary-key order.

        Args:
            table_name: Exact migrated table name.
            chunk_size: Optional positive fetch batch size.

        Yields:
            Exact-column row mappings from the captured read snapshot.

        Returns:
            Async row iterator bounded by the requested fetch batch size.

        Raises:
            SqliteRollbackSourceError: If closed or the source path was replaced.
            ValueError: If the table or chunk size is invalid.

        Complexity:
            O(rows * columns) time and O(chunk_size) row memory.
        """
        connection = self._require_connection()
        if table_name not in MIGRATED_TABLE_ORDER_V1:
            raise ValueError(f"rollback table is not allowlisted: {table_name}")
        size = self._chunk_size if chunk_size is None else chunk_size
        if type(size) is not int or size <= 0:
            raise ValueError("rollback stream chunk_size must be positive")
        try:
            assert_source_identity(self._path, self.source)
            cursor = await connection.execute(_table_query(table_name))
            try:
                while rows := await cursor.fetchmany(size):
                    for row in rows:
                        yield dict(row)
            finally:
                await cursor.close()
        except (OSError, ValueError, aiosqlite.Error) as exc:
            raise SqliteRollbackSourceError(str(exc)) from exc

    async def _capture_manifest(
        self,
        connection: aiosqlite.Connection,
        source: SqliteRollbackSourceIdentity,
    ) -> SqliteRollbackManifest:
        version = await validate_sqlite_v1(connection)
        await validate_sqlite_integrity(connection)
        foreign_keys = await sqlite_foreign_key_failures(connection)
        if foreign_keys:
            raise ValueError(
                f"SQLite rollback foreign key check failed: {foreign_keys}"
            )
        metrics = await read_sqlite_table_metrics(
            connection, chunk_size=self._chunk_size
        )
        aggregate_document = await capture_sqlite_aggregate_manifest(
            connection, as_of_utc=self._as_of_utc
        )
        primary_keys = {
            table.name: table.primary_key
            for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
        }
        return SqliteRollbackManifest(
            manifest_version=ROLLBACK_MANIFEST_VERSION,
            created_at_utc=self._as_of_utc,
            sqlite_schema_version=version,
            schema_registry=canonical_schema_manifest_document(),
            source=source,
            tables=rollback_table_metrics(metrics, primary_keys),
            aggregates=rollback_aggregates(aggregate_document),
        )

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise SqliteRollbackSourceError("rollback source snapshot is closed")
        return self._connection

    async def _close(self) -> None:
        connection = self._connection
        self._connection = None
        self._source = None
        self._manifest = None
        if connection is not None:
            if connection.in_transaction:
                await connection.rollback()
            await connection.close()


def open_sqlite_rollback_snapshot(
    path: Path,
    *,
    as_of_utc: datetime,
    chunk_size: int = 1000,
) -> AbstractAsyncContextManager[SqliteRollbackSnapshot]:
    """Create a closed read-only SQLite rollback snapshot context.

    Args:
        path: Existing closed SQLite-v1 source file.
        as_of_utc: Aware cutoff used by every rolling aggregate.
        chunk_size: Positive hashing and default row-stream batch size.

    Returns:
        Async context manager that validates before yielding its snapshot.

    Raises:
        ValueError: If the cutoff or chunk size is invalid.
    """
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise ValueError("rollback aggregate cutoff must be aware")
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("rollback source chunk_size must be positive")
    return SqliteRollbackSnapshot(path, as_of_utc=as_of_utc, chunk_size=chunk_size)


def _table_query(table_name: str) -> str:
    table = next(
        item for item in CANONICAL_SCHEMA_MANIFEST_V1.tables if item.name == table_name
    )
    columns = ",".join(f'"{column.name}"' for column in table.columns)
    by_name = {column.name: column for column in table.columns}
    order = ",".join(
        (
            f'"{name}" COLLATE BINARY'
            if by_name[name].codec_kind == "text"
            else f'"{name}"'
        )
        for name in table.primary_key
    )
    return f'SELECT {columns} FROM "{table.name}" ORDER BY {order}'


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


__all__ = [
    "SqliteRollbackAggregates",
    "SqliteRollbackManifest",
    "SqliteRollbackSnapshot",
    "SqliteRollbackSourceError",
    "SqliteRollbackSourceIdentity",
    "SqliteRollbackTableMetric",
    "open_sqlite_rollback_snapshot",
]
