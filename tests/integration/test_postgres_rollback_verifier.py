"""Real PostgreSQL rollback verifier evidence."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import psycopg2  # type: ignore[import-untyped]
import pytest

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_baseline_manifest import aggregate_manifest
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_canonical_aggregates import (
    capture_canonical_aggregates,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    MIGRATED_TABLE_ORDER_V1,
    canonical_schema_manifest_document,
)
from jobfeed.adapters.migration.postgres_rollback_verifier import (
    ExpectedCutoverProvenance,
    TableVerificationResult,
    verify_postgres_rollback,
)
from tests.integration.test_sqlite_forward_import_postgres import _seed_postgres
from tests.unit._sqlite_forward_import_fixture import (
    canonical_source_rows,
    snapshot_manifest,
)

_GENERATED_IDS = (
    "jobs",
    "evaluations",
    "pipeline_runs",
    "job_status_history",
    "llm_usage",
    "interview_rounds",
    "step_timings",
)
_AS_OF = datetime(2026, 8, 12, 13, 14, 15, 123456, tzinfo=UTC)


def _reset_sequences(dsn: str) -> None:
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        for table_name in _GENERATED_IDS:
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                f'(SELECT MAX(id) FROM "{table_name}"), true)',
                (table_name,),
            )


def _source_manifest(cutover: dict[str, object]) -> dict[str, object]:
    tables = cutover["tables"]
    assert isinstance(tables, dict)
    return {
        "manifest_version": 1,
        "created_at_utc": "2026-08-12T13:14:15.123456Z",
        "sqlite_schema_version": 1,
        "schema_registry": canonical_schema_manifest_document(),
        "source": {
            "file_size_bytes": 4096,
            "file_sha256": "8" * 64,
            "device": 1,
            "inode": 2,
            "journal_mode": "delete",
            "has_wal": False,
        },
        "tables": [
            {"table_name": name, **copy.deepcopy(tables[name])}
            for name in MIGRATED_TABLE_ORDER_V1
        ],
        "aggregates": copy.deepcopy(cutover["aggregates"]),
    }


def _provenance(
    cutover: dict[str, object], database_identity: str
) -> ExpectedCutoverProvenance:
    tables = cutover["tables"]
    assert isinstance(tables, dict)
    return ExpectedCutoverProvenance(
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
                row_count=int(tables[name]["row_count"]),
                max_identity=tables[name]["max_identity"],
                canonical_sha256=str(tables[name]["canonical_sha256"]),
            )
            for name in MIGRATED_TABLE_ORDER_V1
        ),
    )


@pytest.mark.postgres
def test_real_postgres_exact_rollback_verification(fresh_pg_dsn: str) -> None:
    """A real 0008 database closes every read-only verification gate."""
    rows = canonical_source_rows()
    _seed_postgres(fresh_pg_dsn, rows)
    _reset_sequences(fresh_pg_dsn)

    with PostgresBaselineReader(fresh_pg_dsn) as reader:
        cutover = snapshot_manifest(rows)
        cutover["aggregates"] = aggregate_manifest(
            capture_canonical_aggregates(reader, _AS_OF)
        )
        report = verify_postgres_rollback(
            reader,
            _source_manifest(cutover),
            _provenance(cutover, reader.database_identity()),
            chunk_size=1,
        )

    assert report.is_match
    assert report.mismatches == ()
    assert len(report.tables) == len(MIGRATED_TABLE_ORDER_V1)
    assert len(report.sequences) == len(_GENERATED_IDS)
