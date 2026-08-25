"""Transactional SQLite staging and exact target-parity helpers."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

from jobfeed.adapters.migration._sqlite_forward_values import _sqlite_value
from jobfeed.adapters.migration.canonical_row import CanonicalRowHasher
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
    CanonicalManifestTable,
)
from jobfeed.adapters.store._sqlite_schema_metadata import (
    SQLITE_SCHEMA_VERSION,
    SQLITE_TRIGGER_SQL,
    schema_ddl_statements,
)

_SEED_LEASE_SQL: Final = (
    "INSERT INTO run_leases(kind,generation) VALUES('scan',0),('evaluate',0)"
)


def _create_stage(target: Path) -> Path:
    """Create one importer-owned unpredictable sibling file."""
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.forward-import-",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _remove_stage(stage: Path) -> None:
    """Remove only files whose unpredictable stage name is owned by this run."""
    for candidate in (stage, Path(f"{stage}-wal"), Path(f"{stage}-shm")):
        with contextlib.suppress(FileNotFoundError):
            candidate.unlink()


def _import_rows(
    stage: Path,
    source_rows: Mapping[str, Iterable[dict[str, object]]],
    metrics: Mapping[str, Mapping[str, object]],
    *,
    chunk_size: int,
) -> dict[str, int]:
    """Create v1 and load all migrated rows in one SQLite transaction."""
    connection = sqlite3.connect(stage, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN IMMEDIATE")
        for statement in schema_ddl_statements():
            if statement != SQLITE_TRIGGER_SQL:
                connection.execute(statement)
        connection.execute(_SEED_LEASE_SQL)
        counts = _insert_all(connection, source_rows, metrics, chunk_size)
        connection.execute(SQLITE_TRIGGER_SQL)
        connection.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION}")
        connection.commit()
        return counts
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _insert_all(
    connection: sqlite3.Connection,
    source_rows: Mapping[str, Iterable[dict[str, object]]],
    metrics: Mapping[str, Mapping[str, object]],
    chunk_size: int,
) -> dict[str, int]:
    """Insert and source-hash all rows. Time complexity is O(rows * columns)."""
    counts: dict[str, int] = {}
    for table, schema in zip(
        CANONICAL_SCHEMA_MANIFEST_V1.tables,
        CANONICAL_ROW_SCHEMAS_V1,
        strict=True,
    ):
        names = tuple(column.name for column in table.columns)
        placeholders = ",".join("?" for _ in names)
        quoted_names = ",".join(f'"{name}"' for name in names)
        statement = f'INSERT INTO "{table.name}"({quoted_names}) VALUES({placeholders})'
        hasher = CanonicalRowHasher(schema)
        count = 0
        maximum: int | None = None
        batch: list[tuple[object, ...]] = []
        for row in source_rows[table.name]:
            hasher.update_rows([row])
            batch.append(
                tuple(
                    _sqlite_value(column, row[column.name]) for column in table.columns
                )
            )
            count += 1
            if "id" in table.primary_key:
                identity = row["id"]
                if type(identity) is not int:
                    raise TypeError(f"{table.name} identity must be exact integer")
                maximum = max(maximum or 0, identity)
            if len(batch) == chunk_size:
                connection.executemany(statement, batch)
                batch.clear()
        if batch:
            connection.executemany(statement, batch)
        _require_metric(
            table.name, metrics[table.name], count, maximum, hasher.hexdigest()
        )
        counts[table.name] = count
    return counts


def _validate_stage(
    stage: Path, metrics: Mapping[str, Mapping[str, object]]
) -> dict[str, str]:
    """Rehash the physical SQLite rows and validate all structural invariants."""
    uri = f"{stage.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if _scalar(connection, "PRAGMA user_version") != SQLITE_SCHEMA_VERSION:
            raise ValueError("SQLite import schema version mismatch")
        if _scalar(connection, "PRAGMA integrity_check") != "ok":
            raise ValueError("SQLite import integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite import foreign key check failed")
        _validate_table_set(connection)
        _validate_leases(connection)
        return _validate_table_metrics(connection, metrics)
    finally:
        connection.close()


def _validate_table_metrics(
    connection: sqlite3.Connection,
    metrics: Mapping[str, Mapping[str, object]],
) -> dict[str, str]:
    """Rehash target rows. Time complexity is O(rows * columns)."""
    hashes: dict[str, str] = {}
    for table, schema in zip(
        CANONICAL_SCHEMA_MANIFEST_V1.tables,
        CANONICAL_ROW_SCHEMAS_V1,
        strict=True,
    ):
        columns = ",".join(f'"{column.name}"' for column in table.columns)
        order = _primary_key_order(table)
        cursor = connection.execute(
            f'SELECT {columns} FROM "{table.name}" ORDER BY {order}'
        )
        hasher = CanonicalRowHasher(schema)
        count = 0
        maximum: int | None = None
        for raw_row in cursor:
            row = dict(raw_row)
            hasher.update_rows([row])
            count += 1
            if "id" in table.primary_key:
                maximum = max(maximum or 0, int(row["id"]))
        digest = hasher.hexdigest()
        _require_metric(table.name, metrics[table.name], count, maximum, digest)
        hashes[table.name] = digest
    return hashes


def _require_metric(
    table_name: str,
    metric: Mapping[str, object],
    count: int,
    maximum: int | None,
    digest: str,
) -> None:
    if metric["row_count"] != count:
        raise ValueError(f"{table_name} row count mismatch")
    if metric["canonical_sha256"] != digest:
        raise ValueError(f"{table_name} canonical checksum mismatch")
    if metric["max_identity"] != maximum:
        raise ValueError(f"{table_name} identity maximum mismatch")


def _primary_key_order(table: CanonicalManifestTable) -> str:
    by_name = {column.name: column for column in table.columns}
    return ",".join(
        (
            f'"{name}" COLLATE BINARY'
            if by_name[name].codec_kind == "text"
            else f'"{name}"'
        )
        for name in table.primary_key
    )


def _validate_table_set(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    expected = {table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables}
    expected.update({"run_leases", "evaluation_results"})
    if {str(row[0]) for row in rows} != expected:
        raise ValueError("SQLite import table coverage mismatch")


def _validate_leases(connection: sqlite3.Connection) -> None:
    rows = [
        tuple(row)
        for row in connection.execute(
            "SELECT kind,generation,owner_id,run_id,heartbeat_at,expires_at "
            "FROM run_leases ORDER BY kind"
        ).fetchall()
    ]
    if rows != [
        ("evaluate", 0, None, None, None, None),
        ("scan", 0, None, None, None, None),
    ]:
        raise ValueError("SQLite import run lease seed mismatch")


def _scalar(connection: sqlite3.Connection, sql: str) -> object:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise ValueError(f"SQLite import query returned no row: {sql}")
    return row[0]


def _file_sha256(path: Path) -> str:
    """Hash one closed SQLite file in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_stage(stage: Path, target: Path) -> None:
    """Durably publish without overwriting any existing directory entry."""
    identity = stage.stat()
    descriptor = os.open(stage, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    linked = False
    try:
        os.link(stage, target)
        linked = True
        _sync_directory(target.parent)
        stage.unlink()
        _sync_directory(target.parent)
    except BaseException:
        if linked:
            _remove_owned_publication(target, identity.st_dev, identity.st_ino)
        raise


def _sync_directory(parent: Path) -> None:
    """Persist directory-entry changes for one publication parent."""
    directory = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _remove_owned_publication(target: Path, device: int, inode: int) -> None:
    """Roll back only the hard link proven to name this run's stage inode."""
    with contextlib.suppress(FileNotFoundError, OSError):
        identity = target.lstat()
        if identity.st_dev == device and identity.st_ino == inode:
            target.unlink()
            with contextlib.suppress(OSError):
                _sync_directory(target.parent)
