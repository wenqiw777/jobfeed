"""SQLite v1 DDL constraints, indexes, foreign keys, and trigger tests."""

from __future__ import annotations

import re

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite_schema import SQLITE_METADATA, ensure_sqlite_schema

_EXPLICIT_INDEXES = {
    "idx_companies_vendor",
    "idx_eval_stage_a_score",
    "idx_eval_stage_b_completed",
    "idx_eval_stage_b_queue",
    "idx_interview_rounds_job",
    "idx_interview_rounds_upcoming",
    "idx_jobs_dedup_softkey",
    "idx_jobs_discovered_at",
    "idx_job_status_followup",
    "idx_job_status_history_job",
    "idx_job_status_stale",
    "idx_job_status_status",
    "idx_jsh_applied_at",
    "idx_llm_usage_run",
    "idx_llm_usage_timestamp",
    "idx_step_timings_run",
    "idx_step_timings_type_created",
}
_TIMESTAMP_DEFAULT_COLUMNS = {
    ("evaluations", "created_at"),
    ("evaluations", "updated_at"),
    ("resume_variants", "created_at"),
    ("job_status", "last_status_change_at"),
    ("job_status_history", "changed_at"),
    ("applied", "applied_at"),
    ("resume_snapshots", "captured_at"),
    ("cost_ledger", "last_updated"),
    ("llm_usage", "timestamp"),
    ("interview_rounds", "created_at"),
    ("step_timings", "created_at"),
}
_CANONICAL_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


async def _new_database() -> aiosqlite.Connection:
    connection = await aiosqlite.connect(":memory:")
    await connection.execute("PRAGMA foreign_keys=ON")
    await ensure_sqlite_schema(connection)
    return connection


async def _insert_job(connection: aiosqlite.Connection, canonical_id: str) -> int:
    cursor = await connection.execute(
        "INSERT INTO jobs("
        "platform, canonical_id, url, title, company, location, discovered_at"
        ") VALUES('test', ?, 'https://example.test', 'Engineer', 'Acme', '', "
        "'2026-08-12T00:00:00.000000Z')",
        (canonical_id,),
    )
    row_id = cursor.lastrowid
    await cursor.close()
    assert row_id is not None
    return row_id


@pytest.mark.asyncio
async def test_foreign_keys_and_job_seed_trigger_are_live() -> None:
    """FK enforcement rejects orphans and each job seeds status/history."""
    connection = await _new_database()
    try:
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await connection.execute("INSERT INTO evaluations(job_id) VALUES(999)")

        job_id = await _insert_job(connection, "seed-me")
        cursor = await connection.execute(
            "SELECT status FROM job_status WHERE job_id=?", (job_id,)
        )
        assert await cursor.fetchone() == ("new",)
        await cursor.close()
        cursor = await connection.execute(
            "SELECT from_status, to_status FROM job_status_history WHERE job_id=?",
            (job_id,),
        )
        assert await cursor.fetchall() == [(None, "new")]
        await cursor.close()

        await connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        assert await _count(connection, "job_status") == 0
        assert await _count(connection, "job_status_history") == 0
    finally:
        await connection.close()


async def _count(connection: aiosqlite.Connection, table: str) -> int:
    cursor = await connection.execute(f'SELECT COUNT(*) FROM "{table}"')
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO run_leases(kind, generation) VALUES('other', 0)",
        "UPDATE run_leases SET generation=-1 WHERE kind='scan'",
        "UPDATE run_leases SET owner_id='owner' WHERE kind='scan'",
    ],
)
async def test_run_lease_checks_reject_invalid_permanent_rows(statement: str) -> None:
    """Lease kind, monotonic generation, and occupancy shape are constrained."""
    connection = await _new_database()
    try:
        with pytest.raises(aiosqlite.IntegrityError, match="CHECK"):
            await connection.execute(statement)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_enum_score_boolean_and_usage_checks_are_enforced() -> None:
    """Persisted enums, scores, booleans, and nonnegative metrics fail closed."""
    connection = await _new_database()
    try:
        job_id = await _insert_job(connection, "checks")
        invalid_statements = [
            ("UPDATE jobs SET jd_quality='excellent' WHERE id=?", (job_id,)),
            ("UPDATE jobs SET ml_gate_score=1.1 WHERE id=?", (job_id,)),
            ("UPDATE jobs SET clearance_required=2 WHERE id=?", (job_id,)),
            ("UPDATE jobs SET domain_tags='{' WHERE id=?", (job_id,)),
            ("UPDATE job_status SET status='phone_screen' WHERE job_id=?", (job_id,)),
            (
                "INSERT INTO evaluations(job_id, stage_a_score) VALUES(?, 101)",
                (job_id,),
            ),
            (
                "INSERT INTO llm_usage("
                "model,input_tokens,output_tokens,cost_usd,cached,latency_ms,timestamp"
                ") VALUES('m',-1,0,0,0,0,'2026-08-12T00:00:00Z')",
                (),
            ),
        ]
        for statement, params in invalid_statements:
            with pytest.raises(aiosqlite.IntegrityError, match="CHECK"):
                await connection.execute(statement, params)
    finally:
        await connection.close()


def test_all_database_timestamp_defaults_use_canonical_utc_expression() -> None:
    """Every v1 DB clock default emits the frozen UTC microsecond shape."""
    for table_name, column_name in _TIMESTAMP_DEFAULT_COLUMNS:
        default = SQLITE_METADATA.tables[table_name].c[column_name].server_default
        assert default is not None
        assert str(default.arg) == "strftime('%Y-%m-%dT%H:%M:%f000Z','now')"


@pytest.mark.asyncio
async def test_trigger_timestamps_are_canonical_and_lexical_cutoffs_work() -> None:
    """Trigger timestamps sort after a same-day canonical midnight cutoff."""
    connection = await _new_database()
    try:
        job_id = await _insert_job(connection, "canonical-clock")
        cursor = await connection.execute(
            "SELECT js.last_status_change_at, h.changed_at "
            "FROM job_status js JOIN job_status_history h ON h.job_id=js.job_id "
            "WHERE js.job_id=?",
            (job_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None
        status_timestamp, history_timestamp = row
        assert _CANONICAL_UTC.fullmatch(status_timestamp)
        assert _CANONICAL_UTC.fullmatch(history_timestamp)

        same_day_cutoff = f"{history_timestamp[:10]}T00:00:00.000000Z"
        cursor = await connection.execute(
            "SELECT changed_at FROM job_status_history "
            "WHERE changed_at >= ? ORDER BY changed_at, id",
            (same_day_cutoff,),
        )
        assert await cursor.fetchall() == [(history_timestamp,)]
        await cursor.close()
        assert same_day_cutoff <= status_timestamp
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_explicit_indexes_and_trigger_names_are_exact() -> None:
    """The v1 schema retains every 0008 hot-path index and one seed trigger."""
    connection = await _new_database()
    try:
        cursor = await connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' "
            "AND name NOT LIKE 'sqlite_autoindex_%'"
        )
        assert {row[0] for row in await cursor.fetchall()} == _EXPLICIT_INDEXES
        await cursor.close()
        cursor = await connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger'"
        )
        assert await cursor.fetchall() == [("trg_jobs_seed_status",)]
        await cursor.close()
    finally:
        await connection.close()
