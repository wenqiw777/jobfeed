"""Fail-closed PostgreSQL-0008 snapshot import into a new SQLite-v1 file."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from jobfeed.adapters.migration._sqlite_forward_db import (
    _create_stage,
    _file_sha256,
    _import_rows,
    _publish_stage,
    _remove_stage,
    _validate_stage,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    MIGRATED_TABLE_ORDER_V1,
)
from jobfeed.adapters.migration.pg_baseline import (
    validate_live_schema,
    validate_public_tables,
)
from jobfeed.adapters.migration.snapshot_manifest import validate_snapshot_manifest


class PostgresSnapshotSource(Protocol):
    """Already-open repeatable-read PostgreSQL snapshot required by the importer."""

    def scalar(self, sql: str) -> object:
        """Read one scalar from the active snapshot.

        Args:
            sql: Trusted read-only SQL statement.

        Returns:
            The first column of the first result row.
        """

    def live_schema_document(self) -> dict[str, object]:
        """Return the live registry-shaped PostgreSQL schema.

        Returns:
            Exact schema manifest fields read from PostgreSQL metadata.
        """

    def public_base_tables(self) -> list[str]:
        """Return every public base table visible in the snapshot.

        Returns:
            Public table names, including Alembic's metadata table.
        """

    def stream_table(
        self, table_name: str, chunk_size: int
    ) -> Iterable[dict[str, object]]:
        """Stream one table in canonical primary-key order.

        Args:
            table_name: Exact allowlisted migrated table.
            chunk_size: Positive bounded fetch size.

        Returns:
            Ordered row iterable for the named table.
        """


@dataclass(frozen=True, kw_only=True)
class ForwardImportResult:
    """Immutable physical and logical identity of one published SQLite import."""

    path: Path
    sqlite_file_sha256: str
    row_counts: dict[str, int]
    table_sha256: dict[str, str]


def import_postgres_snapshot_to_sqlite(
    source: PostgresSnapshotSource,
    snapshot_manifest: object,
    destination: Path,
    *,
    chunk_size: int = 1_000,
) -> ForwardImportResult:
    """Import an exact PostgreSQL-0008 snapshot into a new SQLite-v1 file.

    The caller owns the already-open repeatable-read source snapshot. This function
    writes only an unpredictable sibling stage and publishes by a no-replace link;
    it never opens, replaces, or mutates an existing runtime database.

    Args:
        source: Active read-only PostgreSQL snapshot reader.
        snapshot_manifest: Exact baseline manifest bound to the same snapshot.
        destination: New SQLite file path whose parent already exists.
        chunk_size: Positive source fetch and target insert batch size.

    Returns:
        Published file hash plus exact row counts and canonical table hashes.

    Raises:
        ValueError: Source, manifest, schema, data, or parity is inconsistent.
        FileExistsError: Destination already exists, including as a symlink.
        Exception: Source reads and SQLite writes propagate after stage cleanup.
    """
    if chunk_size <= 0:
        raise ValueError("forward import chunk_size must be positive")
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    manifest = validate_snapshot_manifest(snapshot_manifest)
    _validate_source(source)
    metrics = _table_metrics(manifest)
    stage = _create_stage(destination)
    try:
        rows = {
            name: source.stream_table(name, chunk_size)
            for name in MIGRATED_TABLE_ORDER_V1
        }
        counts = _import_rows(stage, rows, metrics, chunk_size=chunk_size)
        table_hashes = _validate_stage(stage, metrics)
        sqlite_hash = _file_sha256(stage)
        _publish_stage(stage, destination)
    except BaseException:
        _remove_stage(stage)
        raise
    return ForwardImportResult(
        path=destination,
        sqlite_file_sha256=sqlite_hash,
        row_counts=counts,
        table_sha256=table_hashes,
    )


def _validate_source(source: PostgresSnapshotSource) -> None:
    revision = source.scalar("SELECT version_num FROM alembic_version")
    if revision != "0008":
        raise ValueError(
            f"forward import requires source revision 0008, got {revision}"
        )
    validate_live_schema(source.live_schema_document())
    try:
        validate_public_tables(source.public_base_tables())
    except ValueError as error:
        raise ValueError(f"source public table contract failed: {error}") from error


def _table_metrics(
    manifest: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    raw = manifest["tables"]
    if not isinstance(raw, dict):
        raise ValueError("snapshot manifest tables must be an object")
    return {
        name: cast(Mapping[str, object], raw[name]) for name in MIGRATED_TABLE_ORDER_V1
    }


__all__ = [
    "ForwardImportResult",
    "PostgresSnapshotSource",
    "import_postgres_snapshot_to_sqlite",
]
