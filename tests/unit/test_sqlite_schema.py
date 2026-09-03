"""SQLite v1 schema creation and version-gate tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)
from jobfeed.adapters.store import sqlite_schema
from jobfeed.adapters.store.sqlite_schema import (
    SQLITE_METADATA,
    SQLITE_SCHEMA_VERSION,
    SQLITE_TABLE_NAMES,
    ensure_sqlite_schema,
)

_RUN_LEASE_ROWS = [
    ("evaluate", 0, None, None, None, None),
    ("scan", 0, None, None, None, None),
]
_INJECTED_FAILURE_CALL = 4
_DATA_REPAIR_FAILURE_CALL = 2


async def _scalar(connection: aiosqlite.Connection, sql: str) -> Any:
    cursor = await connection.execute(sql)
    row = await cursor.fetchone()
    await cursor.close()
    return row[0] if row else None


async def _table_names(connection: aiosqlite.Connection) -> tuple[str, ...]:
    cursor = await connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return tuple(row[0] for row in rows)


def test_metadata_matches_frozen_0008_registry_plus_run_leases() -> None:
    """Core metadata has the exact migrated columns and one lease table."""
    migrated = CANONICAL_SCHEMA_MANIFEST_V1.tables

    assert SQLITE_SCHEMA_VERSION == 1
    expected_names = (*(table.name for table in migrated), "run_leases")
    assert expected_names == SQLITE_TABLE_NAMES
    assert tuple(SQLITE_METADATA.tables) == SQLITE_TABLE_NAMES
    for expected in migrated:
        actual = SQLITE_METADATA.tables[expected.name]
        assert tuple(column.name for column in actual.columns) == tuple(
            column.name for column in expected.columns
        )
        assert (
            tuple(column.name for column in actual.primary_key) == expected.primary_key
        )
        for expected_column in expected.columns:
            actual_column = actual.columns[expected_column.name]
            assert str(actual_column.type) == expected_column.target_sqlite_type
            assert actual_column.nullable is expected_column.nullable


@pytest.mark.asyncio
async def test_empty_database_migrates_atomically_to_v1_and_reopens() -> None:
    """A new database gets exactly 15 tables and two idle permanent leases."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == 1
        assert await _table_names(connection) == tuple(sorted(SQLITE_TABLE_NAMES))
        cursor = await connection.execute(
            "SELECT kind, generation, owner_id, run_id, heartbeat_at, expires_at "
            "FROM run_leases ORDER BY kind"
        )
        assert await cursor.fetchall() == _RUN_LEASE_ROWS
        await cursor.close()

        await connection.execute("INSERT INTO state(key, value) VALUES('kept', 'yes')")
        await connection.commit()
        await ensure_sqlite_schema(connection)
        assert (
            await _scalar(connection, "SELECT value FROM state WHERE key='kept'")
            == "yes"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [2, 99, 2_147_483_647])
async def test_unknown_schema_version_fails_closed(version: int) -> None:
    """Versions newer than v1 are never guessed or modified."""
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="unsupported SQLite schema version"):
            await ensure_sqlite_schema(connection)
        assert await _scalar(connection, "PRAGMA user_version") == version
        assert await _table_names(connection) == ()


@pytest.mark.asyncio
async def test_inconsistent_zero_or_v1_database_fails_closed() -> None:
    """Neither a partial unversioned DB nor a version-only DB is repaired."""
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("CREATE TABLE intruder(id INTEGER PRIMARY KEY)")
        await connection.commit()
        with pytest.raises(ValueError, match="version 0 database is not empty"):
            await ensure_sqlite_schema(connection)
        assert await _table_names(connection) == ("intruder",)

    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("PRAGMA user_version=1")
        with pytest.raises(ValueError, match="schema v1 is inconsistent"):
            await ensure_sqlite_schema(connection)
        assert await _table_names(connection) == ()


@pytest.mark.asyncio
async def test_current_data_repair_backfills_completed_evaluation_times() -> None:
    """Existing completed evaluations gain stable dates without a DDL migration."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute("PRAGMA user_version=1")
        await connection.execute(
            """INSERT INTO jobs(
                   platform, canonical_id, url, title, company, location,
                   discovered_at
               ) VALUES('test','legacy','https://example.test','Engineer',
                        'Example','Remote','2026-05-01T00:00:00.000000Z')"""
        )
        job_id = await _scalar(connection, "SELECT id FROM jobs")
        await connection.execute(
            """INSERT INTO jobs(
                   platform, canonical_id, url, title, company, location,
                   discovered_at
               ) VALUES('test','drift','https://example.test/drift','Engineer',
                        'Example','Remote','2026-05-01T00:00:00.000000Z')"""
        )
        drift_job_id = await _scalar(
            connection, "SELECT id FROM jobs WHERE canonical_id='drift'"
        )
        await connection.execute(
            "UPDATE job_status SET status='scored' WHERE job_id=?", (drift_job_id,)
        )
        await connection.execute(
            """INSERT INTO evaluations(
                   job_id, stage_a_status, stage_a_at, stage_b_status, stage_b_at,
                   created_at, updated_at
               ) VALUES(?, 'completed', NULL, 'completed', NULL, ?, ?)""",
            (
                job_id,
                "2026-05-02T03:04:05.000000Z",
                "2026-05-03T04:05:06.000000Z",
            ),
        )
        await connection.commit()

        await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == 1
        cursor = await connection.execute(
            "SELECT stage_a_at, stage_b_at FROM evaluations WHERE job_id=?", (job_id,)
        )
        assert await cursor.fetchone() == (
            "2026-05-02T03:04:05.000000Z",
            "2026-05-03T04:05:06.000000Z",
        )
        await cursor.close()
        assert (
            await _scalar(
                connection,
                """SELECT COUNT(*) FROM job_status s JOIN evaluations e
               ON e.job_id=s.job_id
               WHERE s.status='new' AND e.stage_a_status='completed'""",
            )
            == 0
        )
        assert (
            await _scalar(
                connection,
                """SELECT COUNT(*) FROM job_status s LEFT JOIN evaluations e
               ON e.job_id=s.job_id
               WHERE s.status='scored' AND e.job_id IS NULL""",
            )
            == 0
        )


@pytest.mark.asyncio
async def test_schema_migration_rejects_an_active_caller_transaction() -> None:
    """Schema creation never commits or rolls back an outer transaction."""
    async with aiosqlite.connect(":memory:") as connection:
        await connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="requires no active transaction"):
            await ensure_sqlite_schema(connection)
        assert connection.in_transaction
        await connection.rollback()


@pytest.mark.asyncio
async def test_two_connections_serialize_empty_schema_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two starters that both observe version zero converge on exact v1."""
    database_path = tmp_path / "concurrent-schema.db"
    contender = await aiosqlite.connect(database_path)
    migrator = await aiosqlite.connect(database_path)
    await contender.execute("PRAGMA busy_timeout=5000")
    await migrator.execute("PRAGMA busy_timeout=5000")
    original_user_objects = sqlite_schema._user_objects
    prelock_check_reached = asyncio.Event()
    migration_finished = asyncio.Event()

    async def hold_legacy_prelock_check(
        connection: aiosqlite.Connection,
    ) -> tuple[str, ...]:
        objects = await original_user_objects(connection)
        if connection is contender and not connection.in_transaction:
            prelock_check_reached.set()
            await migration_finished.wait()
        return objects

    monkeypatch.setattr(sqlite_schema, "_user_objects", hold_legacy_prelock_check)
    contender_task = asyncio.create_task(ensure_sqlite_schema(contender))
    prelock_task = asyncio.create_task(prelock_check_reached.wait())
    try:
        done, _ = await asyncio.wait(
            {contender_task, prelock_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if prelock_task in done:
            await ensure_sqlite_schema(migrator)
            migration_finished.set()
        else:
            prelock_task.cancel()
            await contender_task
            await ensure_sqlite_schema(migrator)
        await contender_task

        assert await _scalar(contender, "PRAGMA user_version") == 1
        assert await _scalar(migrator, "PRAGMA user_version") == 1
        assert await _table_names(contender) == tuple(sorted(SQLITE_TABLE_NAMES))
        assert await _table_names(migrator) == tuple(sorted(SQLITE_TABLE_NAMES))
    finally:
        migration_finished.set()
        prelock_task.cancel()
        await contender.close()
        await migrator.close()


@pytest.mark.asyncio
async def test_v1_reopen_accepts_occupied_leases_but_rejects_schema_tampering() -> None:
    """Runtime lease state is data; missing DDL or permanent rows are corruption."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute(
            "UPDATE run_leases SET generation=1, owner_id='owner', run_id='run', "
            "heartbeat_at='2026-08-12T00:00:00Z', "
            "expires_at='2026-08-12T00:03:00Z' WHERE kind='scan'"
        )
        await connection.commit()
        await ensure_sqlite_schema(connection)

        await connection.execute("DROP INDEX idx_jobs_dedup_softkey")
        await connection.commit()
        with pytest.raises(ValueError, match="schema v1 is inconsistent"):
            await ensure_sqlite_schema(connection)

    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute("DELETE FROM run_leases WHERE kind='evaluate'")
        await connection.commit()
        with pytest.raises(ValueError, match="run lease rows differ"):
            await ensure_sqlite_schema(connection)


@pytest.mark.asyncio
async def test_v1_reopen_installs_the_additive_verdict_index() -> None:
    """Existing v1 databases gain the verdict index without a table migration."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute("DROP INDEX idx_eval_verdict_job")
        await connection.commit()

        await ensure_sqlite_schema(connection)

        assert (
            await _scalar(
                connection,
                "SELECT COUNT(*) FROM sqlite_schema "
                "WHERE type='index' AND name='idx_eval_verdict_job'",
            )
            == 1
        )


@pytest.mark.asyncio
async def test_v1_reopen_installs_the_additive_seniority_counter() -> None:
    """Existing v1 databases gain the seniority counter without data loss."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute(
            "INSERT INTO pipeline_runs(run_id, started_at, source, status) "
            "VALUES('kept', '2026-08-27T00:00:00Z', 'evaluate', 'succeeded')"
        )
        for column in (
            "scan_stats_json",
            "restarted_by_run_id",
            "restart_count",
            "last_progress_at",
            "failed_source",
            "failed_stage",
            "failure_message",
            "failure_code",
            "jobs_seniority_filtered",
        ):
            await connection.execute(f"ALTER TABLE pipeline_runs DROP COLUMN {column}")
        await connection.commit()

        await ensure_sqlite_schema(connection)

        assert (
            await _scalar(
                connection,
                "SELECT jobs_seniority_filtered FROM pipeline_runs WHERE run_id='kept'",
            )
            == 0
        )


@pytest.mark.asyncio
async def test_migration_failure_rolls_back_every_ddl_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected failure leaves user_version zero and no partial tables."""
    calls = 0
    original = sqlite_schema._execute_schema_statement

    async def fail_after_three(connection: aiosqlite.Connection, sql: str) -> None:
        nonlocal calls
        calls += 1
        if calls == _INJECTED_FAILURE_CALL:
            raise RuntimeError("injected schema failure")
        await original(connection, sql)

    monkeypatch.setattr(sqlite_schema, "_execute_schema_statement", fail_after_three)
    async with aiosqlite.connect(":memory:") as connection:
        with pytest.raises(RuntimeError, match="injected schema failure"):
            await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == 0
        assert await _table_names(connection) == ()


@pytest.mark.asyncio
async def test_data_repair_failure_rolls_back_every_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed invariant repair preserves the exact pre-repair data."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)
        await connection.execute(
            """INSERT INTO jobs(
                   platform, canonical_id, url, title, company, location,
                   discovered_at
               ) VALUES('test','rollback','https://example.test/rollback',
                        'Engineer','Example','Remote',
                        '2026-05-01T00:00:00.000000Z')"""
        )
        job_id = await _scalar(connection, "SELECT id FROM jobs")
        await connection.execute(
            """INSERT INTO evaluations(
                   job_id, stage_a_status, stage_a_at, created_at, updated_at
               ) VALUES(?, 'completed', NULL, ?, ?)""",
            (
                job_id,
                "2026-05-02T03:04:05.000000Z",
                "2026-05-03T04:05:06.000000Z",
            ),
        )
        await connection.commit()

        calls = 0
        original = sqlite_schema._execute_data_migration_statement

        async def fail_second(connection: aiosqlite.Connection, sql: str) -> None:
            nonlocal calls
            calls += 1
            if calls == _DATA_REPAIR_FAILURE_CALL:
                raise RuntimeError("injected data repair failure")
            await original(connection, sql)

        monkeypatch.setattr(
            sqlite_schema, "_execute_data_migration_statement", fail_second
        )
        with pytest.raises(RuntimeError, match="injected data repair failure"):
            await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == 1
        assert (
            await _scalar(
                connection,
                "SELECT COUNT(*) FROM evaluations WHERE stage_a_at IS NOT NULL",
            )
            == 0
        )
        assert (
            await _scalar(
                connection,
                "SELECT COUNT(*) FROM job_status WHERE status='new'",
            )
            == 1
        )
        assert await _scalar(connection, "SELECT COUNT(*) FROM job_status_history") == 1
