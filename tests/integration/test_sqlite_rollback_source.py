"""Consistent fail-closed SQLite rollback source snapshot contracts."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.migration.canonical_schema_manifest import (
    MIGRATED_TABLE_ORDER_V1,
)
from jobfeed.adapters.migration.sqlite_rollback_source import (
    SqliteRollbackSourceError,
    open_sqlite_rollback_snapshot,
)
from jobfeed.adapters.store._sqlite_schema_metadata import SQLITE_SCHEMA_VERSION
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema

_AS_OF = datetime(2026, 8, 12, tzinfo=UTC)
_INITIAL_STATE_ROWS = 2


async def test_snapshot_gates_and_streams_current_source(tmp_path: Path) -> None:
    """One read snapshot exposes exact typed identity, metrics, and rows."""
    path = await _source_database(tmp_path)

    async with open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF) as snapshot:
        assert snapshot.schema_version == SQLITE_SCHEMA_VERSION
        assert snapshot.source.file_size_bytes == path.stat().st_size
        assert snapshot.source.file_sha256 == _sha256(path)
        assert snapshot.source.journal_mode == "delete"
        assert snapshot.source.has_wal is False
        assert snapshot.manifest.manifest_version == 1
        assert snapshot.manifest.schema_registry["alembic_revision"] == "0008"
        assert tuple(item.table_name for item in snapshot.table_metrics) == (
            MIGRATED_TABLE_ORDER_V1
        )
        jobs = [row async for row in snapshot.stream_table("jobs", chunk_size=1)]
        state = [row async for row in snapshot.stream_table("state", chunk_size=1)]
        assert jobs[0]["canonical_id"] == "one"
        assert [row["key"] for row in state] == ["Z", "é"]
        assert snapshot.manifest.aggregates.pending_stage_a == 1

    with pytest.raises(SqliteRollbackSourceError, match="closed"):
        _ = [row async for row in snapshot.stream_table("jobs")]


async def test_open_read_snapshot_is_stable_against_later_commits(
    tmp_path: Path,
) -> None:
    """All metrics, aggregates, and streams observe one BEGIN snapshot."""
    path = await _source_database(tmp_path)

    context = open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF)
    snapshot = await context.__aenter__()
    try:
        writer = await aiosqlite.connect(path, isolation_level=None)
        await writer.execute("PRAGMA busy_timeout=5000")
        await writer.execute(
            "INSERT INTO state(key, value) VALUES('later', 'not-visible')"
        )
        await writer.close()

        rows = [row async for row in snapshot.stream_table("state")]
        state_metric = next(
            item for item in snapshot.table_metrics if item.table_name == "state"
        )
        assert [row["key"] for row in rows] == ["Z", "é"]
        assert state_metric.row_count == _INITIAL_STATE_ROWS
    finally:
        with pytest.raises(SqliteRollbackSourceError, match="bytes changed"):
            await context.__aexit__(None, None, None)


@pytest.mark.parametrize(
    "mutation",
    ["version", "extra_table", "changed_column", "active_lease", "foreign_key"],
)
async def test_schema_lease_or_integrity_drift_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Unknown schema and unsafe source state are rejected before streaming."""
    path = await _source_database(tmp_path)
    with sqlite3.connect(path) as connection:
        if mutation == "version":
            connection.execute("PRAGMA user_version=99")
        elif mutation == "extra_table":
            connection.execute("CREATE TABLE shadow(id INTEGER PRIMARY KEY)")
        elif mutation == "changed_column":
            connection.execute("ALTER TABLE state ADD COLUMN shadow TEXT")
        elif mutation == "active_lease":
            connection.execute(
                """UPDATE run_leases SET generation=1, owner_id='owner',
                   run_id='run', heartbeat_at='2026-08-12T00:00:00.000000Z',
                   expires_at='2026-08-12T01:00:00.000000Z' WHERE kind='scan'"""
            )
        else:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("INSERT INTO evaluations(id, job_id) VALUES(1, 999)")

    with pytest.raises(SqliteRollbackSourceError):
        async with open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF):
            pytest.fail("unsafe rollback source was accepted")


async def test_active_wal_or_source_path_swap_fails_closed(tmp_path: Path) -> None:
    """Rollback capture never reads an uncheckpointed WAL or swapped file."""
    path = await _source_database(tmp_path)
    writer = await aiosqlite.connect(path, isolation_level=None)
    await writer.execute("PRAGMA journal_mode=WAL")
    await writer.execute("PRAGMA wal_autocheckpoint=0")
    await writer.execute("INSERT INTO state(key, value) VALUES('wal', 'active')")

    with pytest.raises(SqliteRollbackSourceError, match="WAL"):
        async with open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF):
            pytest.fail("active WAL source was accepted")

    await writer.close()
    await _checkpoint_delete_mode(path)
    context = open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF)
    snapshot = await context.__aenter__()
    replacement = tmp_path / "replacement.sqlite"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(SqliteRollbackSourceError, match="identity"):
        _ = [row async for row in snapshot.stream_table("state")]
    with pytest.raises(SqliteRollbackSourceError, match="identity"):
        await context.__aexit__(None, None, None)


async def test_unknown_table_and_invalid_cutoff_fail_without_writes(
    tmp_path: Path,
) -> None:
    """The source surface is allowlisted and requires an aware UTC cutoff."""
    path = await _source_database(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="aware"):
        async with open_sqlite_rollback_snapshot(path, as_of_utc=datetime(2026, 8, 12)):
            pytest.fail("naive cutoff was accepted")
    async with open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF) as snapshot:
        with pytest.raises(ValueError, match="allowlisted"):
            _ = [row async for row in snapshot.stream_table("run_leases")]

    assert path.read_bytes() == before


async def test_in_place_source_mutation_is_reported_when_snapshot_closes(
    tmp_path: Path,
) -> None:
    """A source cannot silently change after its manifest has been captured."""
    path = await _source_database(tmp_path)
    context = open_sqlite_rollback_snapshot(path, as_of_utc=_AS_OF)
    await context.__aenter__()
    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        original = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([original[0] ^ 1]))

    with pytest.raises(SqliteRollbackSourceError, match="bytes changed"):
        await context.__aexit__(None, None, None)


async def _source_database(tmp_path: Path) -> Path:
    path = tmp_path / "source.sqlite"
    lifecycle = SqliteLifecycle(path, ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        await connection.execute(
            """INSERT INTO jobs(
                   id, platform, canonical_id, url, title, company, location,
                   discovered_at
               ) VALUES(1, 'test', 'one', 'https://example/1', 'Engineer',
                        'Acme', 'NY', '2026-08-01T00:00:00.000000Z')"""
        )
        await connection.executemany(
            "INSERT INTO state(key, value) VALUES(?, ?)",
            [("é", "accent"), ("Z", "ascii")],
        )
        await connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await lifecycle.close()
    await _checkpoint_delete_mode(path)
    return path


async def _checkpoint_delete_mode(path: Path) -> None:
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
