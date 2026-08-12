"""Read-only PostgreSQL rollback verification contracts."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from jobfeed.adapters.migration import postgres_rollback_verifier as verifier_module
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    MIGRATED_TABLE_ORDER_V1,
    canonical_schema_manifest_document,
)
from jobfeed.adapters.migration.postgres_rollback_verifier import (
    ExpectedCutoverProvenance,
    PostgresRollbackVerificationError,
    TableVerificationResult,
    verify_postgres_rollback,
)
from tests.unit._sqlite_forward_import_fixture import (
    canonical_source_rows,
    snapshot_manifest,
)

_DATABASE_IDENTITY = "9" * 64
_GENERATED_IDS = {
    "jobs",
    "evaluations",
    "pipeline_runs",
    "job_status_history",
    "llm_usage",
    "interview_rounds",
    "step_timings",
}


class FakeRollbackReader:
    """Repeatable-read reader double exposing only trusted read operations."""

    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.table_rows = copy.deepcopy(rows)
        self.revision = "0008"
        self.trigger_code = "O"
        self.identity = _DATABASE_IDENTITY
        self.schema = canonical_schema_manifest_document()
        self.tables = sorted((*MIGRATED_TABLE_ORDER_V1, "alembic_version"))
        self.sequence_values = {
            name: int(self.table_rows[name][0]["id"])
            for name in _GENERATED_IDS
        }
        self.sql: list[str] = []

    def scalar(self, sql: str, params: tuple[object, ...] = ()) -> object:
        self.sql.append(sql)
        if sql == "SELECT version_num FROM alembic_version":
            return self.revision
        if sql.startswith("SELECT MAX(id) FROM"):
            table_name = sql.split('"')[1]
            return max(int(row["id"]) for row in self.table_rows[table_name])
        raise AssertionError(f"unexpected scalar query: {sql} {params}")

    def rows(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[dict[str, object]]:
        self.sql.append(sql)
        if "FROM pg_trigger" in sql:
            return [{"tgenabled": self.trigger_code}]
        if "FROM pg_sequences" in sql:
            table_name = str(params[0])
            return [
                {
                    "sequence_name": f"public.{table_name}_id_seq",
                    "last_value": self.sequence_values[table_name],
                }
            ]
        raise AssertionError(f"unexpected rows query: {sql} {params}")

    def stream_table(self, table_name: str, chunk_size: int) -> Any:
        assert chunk_size > 0
        yield from copy.deepcopy(self.table_rows[table_name])

    def live_schema_document(self) -> dict[str, object]:
        return copy.deepcopy(self.schema)

    def public_base_tables(self) -> list[str]:
        return list(self.tables)

    def database_identity(self) -> str:
        return self.identity


def _source_manifest(cutover: dict[str, object]) -> dict[str, object]:
    return {
        "manifest_version": 1,
        "created_at_utc": "2026-08-12T15:00:00.000000Z",
        "sqlite_schema_version": 1,
        "schema_registry": canonical_schema_manifest_document(),
        "source": {
            "file_size_bytes": 4096,
            "file_sha256": "8" * 64,
            "device": 1,
            "inode": 2,
            "journal_mode": "wal",
            "has_wal": False,
        },
        "tables": [
            {
                "table_name": name,
                **copy.deepcopy(cutover["tables"][name]),
            }
            for name in MIGRATED_TABLE_ORDER_V1
        ],
        "aggregates": copy.deepcopy(cutover["aggregates"]),
    }


def _provenance(cutover: dict[str, object]) -> ExpectedCutoverProvenance:
    table_documents = cutover["tables"]
    assert isinstance(table_documents, dict)
    return ExpectedCutoverProvenance(
        proof_version=1,
        cutover_manifest=cutover,
        cutover_manifest_sha256=artifact_sha256(cutover),
        target_database_identity=_DATABASE_IDENTITY,
        target_alembic_revision="0008",
        trigger_name="trg_jobs_seed_status",
        trigger_enabled=True,
        pre_import_tables=tuple(
            TableVerificationResult(
                table_name=name,
                row_count=int(table_documents[name]["row_count"]),
                max_identity=table_documents[name]["max_identity"],
                canonical_sha256=str(table_documents[name]["canonical_sha256"]),
            )
            for name in MIGRATED_TABLE_ORDER_V1
        ),
    )


def _aggregate_capture(_reader: object, as_of: datetime) -> dict[str, object]:
    assert as_of == datetime(2026, 8, 12, 13, 14, 15, 123456, tzinfo=UTC)
    return {
        "as_of_utc": as_of,
        "window_days": 30,
        "pending_stage_a": 0,
        "pending_stage_b": 0,
        "needs_attention": {},
        "funnel": [],
        "daily_cost": [],
        "llm_percentiles": [],
    }


def _evidence() -> tuple[
    FakeRollbackReader,
    dict[str, object],
    ExpectedCutoverProvenance,
]:
    rows = canonical_source_rows()
    cutover = snapshot_manifest(rows)
    return FakeRollbackReader(rows), _source_manifest(cutover), _provenance(cutover)


def test_exact_rollback_returns_typed_read_only_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema, data, aggregates, sequences, and provenance all close together."""
    reader, source, provenance = _evidence()
    aggregate_document = copy.deepcopy(source["aggregates"])
    monkeypatch.setattr(
        verifier_module,
        "_capture_aggregate_manifest",
        lambda _reader, _as_of: aggregate_document,
    )

    report = verify_postgres_rollback(reader, source, provenance, chunk_size=1)

    assert report.is_match
    assert report.alembic_revision == "0008"
    assert report.trigger_enabled
    assert report.database_identity == _DATABASE_IDENTITY
    assert report.source_manifest_sha256 == artifact_sha256(source)
    assert report.cutover_manifest_sha256 == provenance.cutover_manifest_sha256
    assert tuple(table.table_name for table in report.tables) == (
        MIGRATED_TABLE_ORDER_V1
    )
    assert {sequence.table_name for sequence in report.sequences} == _GENERATED_IDS
    assert all(
        query.lstrip().upper().startswith(("SELECT", "SHOW")) for query in reader.sql
    )


@pytest.mark.parametrize(
    ("mutation", "scope"),
    [
        ("revision", "postgres_schema"),
        ("tables", "postgres_schema"),
        ("schema", "postgres_schema"),
        ("trigger", "trigger"),
        ("sequence", "sequence"),
        ("table_hash", "table"),
        ("aggregate", "aggregate"),
    ],
)
def test_target_drift_fails_closed_with_typed_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    scope: str,
) -> None:
    """No post-import schema, row, sequence, trigger, or aggregate drift passes."""
    reader, source, provenance = _evidence()
    aggregate_document = copy.deepcopy(source["aggregates"])
    if mutation == "revision":
        reader.revision = "0007"
    elif mutation == "tables":
        reader.tables.append("unexpected")
    elif mutation == "schema":
        reader.schema["alembic_revision"] = "0007"
    elif mutation == "trigger":
        reader.trigger_code = "D"
    elif mutation == "sequence":
        reader.sequence_values["jobs"] = 40
    elif mutation == "table_hash":
        reader.table_rows["state"][0]["value"] = "changed"
    else:
        aggregate_document["pending_stage_a"] = 99
    monkeypatch.setattr(
        verifier_module,
        "_capture_aggregate_manifest",
        lambda _reader, _as_of: aggregate_document,
    )

    with pytest.raises(PostgresRollbackVerificationError) as raised:
        verify_postgres_rollback(reader, source, provenance)

    assert raised.value.report.mismatches[0].scope == scope
    assert not raised.value.report.is_match


def test_cutover_conflict_proof_must_match_manifest_and_live_database() -> None:
    """A self-inconsistent or wrong-database pre-import proof is rejected first."""
    reader, source, provenance = _evidence()
    wrong_table = replace(provenance.pre_import_tables[0], row_count=999)
    changed_proof = replace(
        provenance,
        pre_import_tables=(wrong_table, *provenance.pre_import_tables[1:]),
    )
    with pytest.raises(PostgresRollbackVerificationError) as raised:
        verify_postgres_rollback(reader, source, changed_proof)
    assert raised.value.report.mismatches[0].scope == "cutover_provenance"
    assert reader.sql == []

    wrong_database = replace(provenance, target_database_identity="7" * 64)
    with pytest.raises(PostgresRollbackVerificationError) as raised:
        verify_postgres_rollback(reader, source, wrong_database)
    assert raised.value.report.mismatches[0].subject == "database_identity"


def test_source_manifest_unknown_shape_and_active_wal_fail_before_postgres() -> None:
    """Rollback verification accepts only the exact closed SQLite source evidence."""
    reader, source, provenance = _evidence()
    source["unknown"] = True
    with pytest.raises(PostgresRollbackVerificationError) as raised:
        verify_postgres_rollback(reader, source, provenance)
    assert raised.value.report.mismatches[0].scope == "source_manifest"
    assert reader.sql == []

    reader, source, provenance = _evidence()
    source_document = source["source"]
    assert isinstance(source_document, dict)
    source_document["has_wal"] = True
    with pytest.raises(PostgresRollbackVerificationError):
        verify_postgres_rollback(reader, source, provenance)
    assert reader.sql == []
