"""Real PostgreSQL-0008 rollback replay and failure atomicity evidence."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import psycopg2  # type: ignore[import-untyped]
import pytest

import jobfeed.adapters.migration.postgres_rollback_writer as writer_module
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_baseline_manifest import (
    aggregate_manifest,
    table_metrics,
)
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_canonical_aggregates import (
    capture_canonical_aggregates,
)
from jobfeed.adapters.migration._sqlite_rollback_types import (
    SqliteRollbackTableMetric,
)
from jobfeed.adapters.migration.canonical_row import canonical_rows_sha256
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
)
from jobfeed.adapters.migration.postgres_rollback_verifier import (
    ExpectedCutoverProvenance,
    TableVerificationResult,
    verify_postgres_rollback,
)
from jobfeed.adapters.migration.postgres_rollback_writer import (
    PostgresRollbackError,
    RollbackFaultPoint,
    RollbackWriterConfig,
    replay_snapshot_to_postgres,
)
from jobfeed.adapters.migration.sqlite_forward_import import (
    import_postgres_snapshot_to_sqlite,
)
from jobfeed.adapters.migration.sqlite_rollback_source import (
    open_sqlite_rollback_snapshot,
)
from tests.integration.test_sqlite_forward_import_postgres import _seed_postgres
from tests.unit._sqlite_forward_import_fixture import (
    FakeSnapshotSource,
    canonical_source_rows,
    snapshot_manifest,
)

_CUTOVER_JOB_ID = 41
_ROLLBACK_AS_OF = datetime(2026, 8, 12, 13, 14, 15, tzinfo=UTC)


class CanonicalSnapshot:
    """Async canonical source used to exercise the production writer boundary."""

    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.rows = rows
        self.table_metrics = tuple(
            SqliteRollbackTableMetric(
                table_name=table.name,
                primary_key=table.primary_key,
                row_count=len(rows[table.name]),
                max_identity=(
                    max(int(row["id"]) for row in rows[table.name])
                    if any(column.name == "id" for column in table.columns)
                    and rows[table.name]
                    else None
                ),
                canonical_sha256=canonical_rows_sha256(schema, rows[table.name]),
            )
            for table, schema in zip(
                CANONICAL_SCHEMA_MANIFEST_V1.tables,
                CANONICAL_ROW_SCHEMAS_V1,
                strict=True,
            )
        )

    async def stream_table(
        self, table_name: str, *, chunk_size: int | None = None
    ) -> AsyncIterator[dict[str, object]]:
        assert chunk_size is not None and chunk_size > 0
        for row in self.rows[table_name]:
            yield row


def _cutover_manifest(dsn: str) -> dict[str, object]:
    rows = canonical_source_rows()
    manifest = snapshot_manifest(rows)
    with PostgresBaselineReader(dsn) as reader:
        manifest["tables"] = table_metrics(reader, 1)
    return manifest


async def _trigger_enabled(dsn: str) -> bool:
    connection = await asyncpg.connect(dsn)
    try:
        return bool(
            await connection.fetchval(
                "SELECT tgenabled='O' FROM pg_trigger "
                "WHERE tgrelid='jobs'::regclass AND tgname='trg_jobs_seed_status' "
                "AND NOT tgisinternal"
            )
        )
    finally:
        await connection.close()


async def _jobs_sequence_state(dsn: str) -> tuple[int, bool]:
    connection = await asyncpg.connect(dsn)
    try:
        row = await connection.fetchrow(
            "SELECT last_value,is_called FROM public.jobs_id_seq"
        )
        assert row is not None
        return int(row["last_value"]), bool(row["is_called"])
    finally:
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_replays_insert_update_delete_resets_sequences_and_trigger(
    fresh_pg_dsn: str,
) -> None:
    """One global transaction reconciles the cutover target to final source rows."""
    initial_rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, initial_rows)
    cutover = _cutover_manifest(fresh_pg_dsn)
    final_rows = canonical_source_rows()
    final_rows["jobs"][0]["title"] = "Updated title"
    final_rows["state"] = []
    final_rows["companies"][0]["notes"] = "changed"

    report = await replay_snapshot_to_postgres(
        CanonicalSnapshot(final_rows),
        cutover_manifest=cutover,
        config=RollbackWriterConfig(dsn=fresh_pg_dsn, chunk_size=1),
    )

    assert report.revision == "0008"
    assert not report.target_was_empty
    assert report.trigger_name == "trg_jobs_seed_status"
    assert report.trigger_was_enabled and report.trigger_is_enabled
    assert report.pre_import_table_metrics == cutover["tables"]
    assert report.deleted_rows == {"state": 1}
    assert report.replayed_rows["jobs"] == 1
    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        assert reader.scalar("SELECT title FROM jobs WHERE id=41") == "Updated title"
        assert reader.scalar("SELECT COUNT(*) FROM state") == 0
        actual = table_metrics(reader, 1)
    assert actual == report.final_table_metrics
    assert await _trigger_enabled(fresh_pg_dsn)

    connection = await asyncpg.connect(fresh_pg_dsn)
    try:
        new_job_id = await connection.fetchval(
            "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
            "discovered_at) VALUES('indeed','after-rollback','u','t','c','l',now()) "
            "RETURNING id"
        )
        assert new_job_id > _CUTOVER_JOB_ID
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_status WHERE job_id=$1", new_job_id
            )
            == 1
        )
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_status_history WHERE job_id=$1", new_job_id
            )
            == 1
        )
    finally:
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_typed_sqlite_snapshot_replays_into_empty_postgres_target(
    fresh_pg_dsn: str, tmp_path: Path
) -> None:
    """Task5A's real read snapshot supplies exact metrics and all 14 row streams."""
    rows = canonical_source_rows()
    sqlite_path = tmp_path / "rollback-source.db"
    import_postgres_snapshot_to_sqlite(
        FakeSnapshotSource(rows), snapshot_manifest(rows), sqlite_path, chunk_size=1
    )

    async with open_sqlite_rollback_snapshot(
        sqlite_path,
        as_of_utc=datetime(2026, 8, 12, 13, 14, 15, tzinfo=UTC),
        chunk_size=1,
    ) as source:
        report = await replay_snapshot_to_postgres(
            source,
            cutover_manifest=snapshot_manifest(rows),
            config=RollbackWriterConfig(dsn=fresh_pg_dsn, chunk_size=1),
        )

    assert report.target_was_empty
    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        assert table_metrics(reader, 1) == report.final_table_metrics
    assert await _trigger_enabled(fresh_pg_dsn)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_sqlite_changes_replay_and_pass_read_only_postgres_verifier(
    fresh_pg_dsn: str, tmp_path: Path
) -> None:
    """A changed SQLite snapshot round-trips with exact reverse parity."""
    cutover_rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, cutover_rows)
    cutover = snapshot_manifest(cutover_rows)
    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        cutover["tables"] = table_metrics(reader, 1)
        cutover["aggregates"] = aggregate_manifest(
            capture_canonical_aggregates(reader, _ROLLBACK_AS_OF)
        )
        database_identity = reader.database_identity()

    final_rows = canonical_source_rows()
    final_rows["jobs"][0]["title"] = "Changed while on SQLite"
    final_rows["state"] = []
    sqlite_path = tmp_path / "final-sqlite.db"
    import_postgres_snapshot_to_sqlite(
        FakeSnapshotSource(final_rows),
        snapshot_manifest(final_rows),
        sqlite_path,
        chunk_size=1,
    )

    async with open_sqlite_rollback_snapshot(
        sqlite_path,
        as_of_utc=_ROLLBACK_AS_OF,
        chunk_size=1,
    ) as source:
        writer_report = await replay_snapshot_to_postgres(
            source,
            cutover_manifest=cutover,
            config=RollbackWriterConfig(dsn=fresh_pg_dsn, chunk_size=1),
        )
        proof = ExpectedCutoverProvenance(
            proof_version=1,
            cutover_manifest=cutover,
            cutover_manifest_sha256=artifact_sha256(cutover),
            target_database_identity=database_identity,
            target_alembic_revision="0008",
            trigger_name="trg_jobs_seed_status",
            trigger_enabled=True,
            pre_import_tables=tuple(
                TableVerificationResult(
                    table_name=name,
                    row_count=int(metric["row_count"]),
                    max_identity=metric["max_identity"],
                    canonical_sha256=str(metric["canonical_sha256"]),
                )
                for name, metric in writer_report.pre_import_table_metrics.items()
            ),
        )
        with PostgresBaselineReader(fresh_pg_dsn) as reader:
            verification = verify_postgres_rollback(
                reader,
                source.manifest,
                proof,
                chunk_size=1,
            )

    assert verification.is_match
    assert verification.mismatches == ()
    assert writer_report.deleted_rows == {"state": 1}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rejects_revision_trigger_quiescence_and_target_divergence(
    fresh_pg_dsn: str,
) -> None:
    """Every preflight mismatch stops before trigger or target data changes."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    cutover = _cutover_manifest(fresh_pg_dsn)
    connection = psycopg2.connect(fresh_pg_dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE state SET value='target-extra-write'")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostgresRollbackError, match="divergence"):
        await replay_snapshot_to_postgres(
            CanonicalSnapshot(rows),
            cutover_manifest=cutover,
            config=RollbackWriterConfig(dsn=fresh_pg_dsn, chunk_size=1),
        )
    assert await _trigger_enabled(fresh_pg_dsn)


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "message"),
    [
        ("revision", "revision 0008"),
        ("trigger", "initially be enabled"),
        ("schema", "schema manifest mismatch"),
        ("running", "not quiescent"),
    ],
)
async def test_exact_target_preflight_gates_fail_before_replay(
    blank_migrated_dsn: str, gate: str, message: str
) -> None:
    """Revision, live DDL, named trigger, and quiescence are independent gates."""
    rows = canonical_source_rows()
    _seed_postgres(blank_migrated_dsn, rows)
    cutover = _cutover_manifest(blank_migrated_dsn)
    connection = await asyncpg.connect(blank_migrated_dsn)
    try:
        if gate == "revision":
            await connection.execute("UPDATE alembic_version SET version_num='0007'")
        elif gate == "trigger":
            await connection.execute(
                "ALTER TABLE jobs DISABLE TRIGGER trg_jobs_seed_status"
            )
        elif gate == "schema":
            await connection.execute("ALTER TABLE state ADD COLUMN drift TEXT")
        else:
            await connection.execute(
                "UPDATE pipeline_runs SET status='running' WHERE id=61"
            )
    finally:
        await connection.close()

    with pytest.raises(PostgresRollbackError, match=message):
        await replay_snapshot_to_postgres(
            CanonicalSnapshot(rows),
            cutover_manifest=cutover,
            config=RollbackWriterConfig(dsn=blank_migrated_dsn, chunk_size=1),
        )

    reader = await asyncpg.connect(blank_migrated_dsn)
    try:
        assert await reader.fetchval("SELECT title FROM jobs WHERE id=41") == (
            "Engineer 雪"
        )
    finally:
        await reader.close()


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        RollbackFaultPoint.PREFLIGHT,
        RollbackFaultPoint.AFTER_TRIGGER_DISABLE,
        RollbackFaultPoint.AFTER_JOBS,
        RollbackFaultPoint.MID_REPLAY,
        RollbackFaultPoint.AFTER_SEQUENCE_RESET,
        RollbackFaultPoint.BEFORE_TRIGGER_ENABLE,
        RollbackFaultPoint.TRIGGER_ENABLE,
    ],
)
async def test_any_replay_fault_rolls_back_data_sequences_and_trigger(
    fresh_pg_dsn: str, fault: RollbackFaultPoint
) -> None:
    """Faults leave exact cutover rows, sequences, and enabled trigger intact."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    cutover = _cutover_manifest(fresh_pg_dsn)
    final_rows = canonical_source_rows()
    final_rows["jobs"][0]["title"] = "must-not-commit"
    original_sequence = await _jobs_sequence_state(fresh_pg_dsn)

    with pytest.raises(RuntimeError, match="injected"):
        await replay_snapshot_to_postgres(
            CanonicalSnapshot(final_rows),
            cutover_manifest=cutover,
            config=RollbackWriterConfig(
                dsn=fresh_pg_dsn,
                chunk_size=1,
                fault_point=fault,
            ),
        )

    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        assert table_metrics(reader, 1) == cutover["tables"]
    assert await _jobs_sequence_state(fresh_pg_dsn) == original_sequence
    assert await _trigger_enabled(fresh_pg_dsn)

    connection = await asyncpg.connect(fresh_pg_dsn)
    try:
        new_job_id = await connection.fetchval(
            "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
            "discovered_at) VALUES('indeed',$1,'u','t','c','l',now()) RETURNING id",
            f"post-fault-{fault.value}",
        )
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_status WHERE job_id=$1", new_job_id
            )
            == 1
        )
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_status_history WHERE job_id=$1", new_job_id
            )
            == 1
        )
    finally:
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_failure_during_sequence_reset_restores_nontransactional_sequence(
    fresh_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial setval failure compensates every nontransactional sequence."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    cutover = _cutover_manifest(fresh_pg_dsn)
    original_sequence = await _jobs_sequence_state(fresh_pg_dsn)

    async def fail_during_sequence_reset(connection: asyncpg.Connection) -> None:
        await connection.execute("ALTER SEQUENCE jobs_id_seq RESTART WITH 9999")
        raise RuntimeError("injected partial sequence reset")

    monkeypatch.setattr(writer_module, "_reset_sequences", fail_during_sequence_reset)
    with pytest.raises(RuntimeError, match="partial sequence reset"):
        await replay_snapshot_to_postgres(
            CanonicalSnapshot(rows),
            cutover_manifest=cutover,
            config=RollbackWriterConfig(dsn=fresh_pg_dsn, chunk_size=1),
        )

    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        assert table_metrics(reader, 1) == cutover["tables"]
    assert await _jobs_sequence_state(fresh_pg_dsn) == original_sequence
    assert await _trigger_enabled(fresh_pg_dsn)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_source_metric_mismatch_rolls_back_replayed_rows(
    fresh_pg_dsn: str,
) -> None:
    """Source rows cannot commit unless they match same-snapshot typed metrics."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    cutover = _cutover_manifest(fresh_pg_dsn)
    source = CanonicalSnapshot(rows)
    source.table_metrics = (
        replace(source.table_metrics[0], canonical_sha256="0" * 64),
        *source.table_metrics[1:],
    )

    with pytest.raises(PostgresRollbackError, match="final source parity"):
        await replay_snapshot_to_postgres(
            source,
            cutover_manifest=cutover,
            config=RollbackWriterConfig(dsn=fresh_pg_dsn, chunk_size=1),
        )

    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        assert table_metrics(reader, 1) == cutover["tables"]
    assert await _trigger_enabled(fresh_pg_dsn)
