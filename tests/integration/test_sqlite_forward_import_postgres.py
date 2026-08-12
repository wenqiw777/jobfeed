"""Real PostgreSQL snapshot to physical SQLite forward-import evidence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg2  # type: ignore[import-untyped]
import pytest

from jobfeed.adapters.migration._pg_baseline_manifest import aggregate_manifest
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_canonical_aggregates import (
    capture_canonical_aggregates,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    MIGRATED_TABLE_ORDER_V1,
    CanonicalManifestColumn,
)
from jobfeed.adapters.migration.sqlite_forward_import import (
    import_postgres_snapshot_to_sqlite,
)
from jobfeed.adapters.migration.sqlite_parity import (
    SqliteParityVerificationError,
    verify_sqlite_parity,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.unit._sqlite_forward_import_fixture import (
    canonical_source_rows,
    snapshot_manifest,
)


def _postgres_value(column: CanonicalManifestColumn, value: object) -> object:
    if column.source_sql_type == "integer" and type(value) is bool:
        return int(value)
    return value


def _seed_postgres(dsn: str, rows: dict[str, list[dict[str, object]]]) -> None:
    connection = psycopg2.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE jobs DISABLE TRIGGER trg_jobs_seed_status")
            for table in CANONICAL_SCHEMA_MANIFEST_V1.tables:
                names = tuple(column.name for column in table.columns)
                quoted = ",".join(f'"{name}"' for name in names)
                placeholders = ",".join("%s" for _ in names)
                cursor.executemany(
                    f'INSERT INTO "{table.name}"({quoted}) VALUES({placeholders})',
                    [
                        tuple(
                            _postgres_value(column, row[column.name])
                            for column in table.columns
                        )
                        for row in rows[table.name]
                    ],
                )
            cursor.execute("ALTER TABLE jobs ENABLE TRIGGER trg_jobs_seed_status")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


@pytest.mark.postgres
def test_real_postgres_0008_snapshot_imports_to_physical_sqlite(
    fresh_pg_dsn: str, tmp_path: Path
) -> None:
    """The concrete reader moves all 153 columns with exact target parity."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    target = tmp_path / "from-postgres.db"

    with PostgresBaselineReader(fresh_pg_dsn) as source:
        result = import_postgres_snapshot_to_sqlite(
            source,
            snapshot_manifest(rows),
            target,
            chunk_size=1,
        )

    connection = sqlite3.connect(target)
    try:
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in MIGRATED_TABLE_ORDER_V1
        }
        assert counts == dict.fromkeys(MIGRATED_TABLE_ORDER_V1, 1)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert result.row_counts == counts
        assert len(result.table_sha256) == len(MIGRATED_TABLE_ORDER_V1)
    finally:
        connection.close()


@pytest.mark.postgres
async def test_real_postgres_import_passes_exact_sqlite_parity(
    fresh_pg_dsn: str, tmp_path: Path
) -> None:
    """One source snapshot imports and passes all table and aggregate parity."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    target = tmp_path / "verified-from-postgres.db"

    with PostgresBaselineReader(fresh_pg_dsn) as source:
        manifest = snapshot_manifest(rows)
        source_raw = capture_canonical_aggregates(source, source.database_clock())
        manifest["aggregates"] = aggregate_manifest(source_raw)
        import_postgres_snapshot_to_sqlite(source, manifest, target, chunk_size=1)

    lifecycle = SqliteLifecycle(target, ensure_sqlite_schema)
    await lifecycle.open()
    try:
        try:
            report = await verify_sqlite_parity(lifecycle, manifest, chunk_size=1)
        except SqliteParityVerificationError as error:
            pytest.fail(f"unexpected real parity mismatch: {error.report.mismatches!r}")
    finally:
        await lifecycle.close()

    assert report.is_match
    assert len(report.tables) == len(MIGRATED_TABLE_ORDER_V1)
    assert report.mismatches == ()
