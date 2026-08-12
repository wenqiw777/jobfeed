"""Exact SQLite-source and cutover-proof validation for rollback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from typing import cast

from jobfeed.adapters.migration import _baseline_evidence_shape as shape
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_rollback_verifier_types import (
    ExpectedCutoverProvenance,
    TableVerificationResult,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    MIGRATED_TABLE_ORDER_V1,
    validate_schema_manifest,
)
from jobfeed.adapters.migration.snapshot_manifest import validate_snapshot_manifest

_SOURCE_ROOT_KEYS = {
    "manifest_version",
    "created_at_utc",
    "sqlite_schema_version",
    "schema_registry",
    "source",
    "tables",
    "aggregates",
}
_SOURCE_IDENTITY_KEYS = {
    "file_size_bytes",
    "file_sha256",
    "device",
    "inode",
    "journal_mode",
    "has_wal",
}
_SOURCE_TABLE_KEYS = {
    "table_name",
    "primary_key",
    "row_count",
    "max_identity",
    "canonical_sha256",
}
_GENERATED_ID_TABLES = {
    "jobs",
    "evaluations",
    "pipeline_runs",
    "job_status_history",
    "llm_usage",
    "interview_rounds",
    "step_timings",
}
_AGGREGATE_WINDOW_DAYS = 30


@dataclass(frozen=True, kw_only=True)
class ValidatedRollbackSource:
    """Normalized exact evidence captured from the closed SQLite source."""

    document: dict[str, object]
    manifest_sha256: str
    tables: tuple[TableVerificationResult, ...]
    aggregates: dict[str, object]
    aggregate_as_of: datetime


def validate_rollback_source(value: object) -> ValidatedRollbackSource:
    """Validate the exact Task-5 SQLite rollback source manifest.

    Args:
        value: Parsed mapping or frozen dataclass manifest.

    Returns:
        Normalized source evidence for live target comparison.

    Raises:
        ValueError: If shape, schema, file state, tables, or aggregates differ.
    """
    document = _document(value, "SQLite rollback source manifest")
    shape.exact_keys(document, _SOURCE_ROOT_KEYS, "SQLite rollback source manifest")
    if document["manifest_version"] != 1:
        raise ValueError("unknown SQLite rollback manifest version")
    if document["sqlite_schema_version"] != 1:
        raise ValueError("SQLite rollback source schema must equal v1")
    shape.text(document["created_at_utc"], "rollback manifest created_at")
    validate_schema_manifest(
        shape.mapping(document["schema_registry"], "rollback schema registry")
    )
    _validate_source_identity(document["source"])
    tables = _validate_source_tables(document["tables"])
    aggregates = _validate_aggregates(document["aggregates"])
    return ValidatedRollbackSource(
        document=document,
        manifest_sha256=artifact_sha256(document),
        tables=tables,
        aggregates=aggregates,
        aggregate_as_of=_timestamp(aggregates["as_of_utc"]),
    )


def validate_cutover_provenance(proof: ExpectedCutoverProvenance) -> str:
    """Validate a same-database pre-import proof against the cutover manifest.

    Args:
        proof: Typed proof emitted by the rollback writer preflight.

    Returns:
        Validated cutover manifest SHA-256.

    Raises:
        ValueError: If the proof is incomplete or diverges from the manifest.
    """
    if proof.proof_version != 1:
        raise ValueError("unknown cutover conflict proof version")
    manifest = validate_snapshot_manifest(proof.cutover_manifest)
    manifest_sha = artifact_sha256(manifest)
    if shape.sha(proof.cutover_manifest_sha256, "cutover manifest SHA") != manifest_sha:
        raise ValueError("cutover proof manifest hash mismatch")
    shape.sha(proof.target_database_identity, "cutover target database identity")
    if proof.target_alembic_revision != "0008":
        raise ValueError("cutover target revision must equal 0008")
    if (
        proof.trigger_name != "trg_jobs_seed_status"
        or proof.trigger_enabled is not True
    ):
        raise ValueError("cutover proof requires the enabled named seed trigger")
    observed = _validated_result_tables(proof.pre_import_tables)
    expected = _manifest_table_results(manifest)
    if observed != expected:
        raise ValueError("cutover pre-import table proof mismatches manifest")
    return manifest_sha


def _validate_source_identity(value: object) -> None:
    source = shape.mapping(value, "rollback source identity")
    shape.exact_keys(source, _SOURCE_IDENTITY_KEYS, "rollback source identity")
    shape.integer(source["file_size_bytes"], "rollback SQLite file size", minimum=1)
    shape.sha(source["file_sha256"], "rollback SQLite file SHA")
    shape.integer(source["device"], "rollback SQLite device")
    shape.integer(source["inode"], "rollback SQLite inode")
    journal_mode = shape.text(source["journal_mode"], "rollback SQLite journal mode")
    if journal_mode == "wal":
        raise ValueError("rollback SQLite source journal mode must not be WAL")
    if source["has_wal"] is not False:
        raise ValueError("rollback SQLite source must not have a live WAL")


def _validate_source_tables(value: object) -> tuple[TableVerificationResult, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("rollback source tables must be an ordered sequence")
    documents = tuple(
        shape.mapping(item, f"rollback source tables[{index}]")
        for index, item in enumerate(value)
    )
    names = tuple(document.get("table_name") for document in documents)
    if names != MIGRATED_TABLE_ORDER_V1:
        raise ValueError("rollback source table order or coverage mismatch")
    results = []
    for document, schema in zip(
        documents, CANONICAL_SCHEMA_MANIFEST_V1.tables, strict=True
    ):
        shape.exact_keys(document, _SOURCE_TABLE_KEYS, f"rollback {schema.name}")
        if document["primary_key"] not in (
            list(schema.primary_key),
            schema.primary_key,
        ):
            raise ValueError(f"rollback primary key mismatch: {schema.name}")
        results.append(_table_result(document, schema.name))
    return tuple(results)


def _validate_aggregates(value: object) -> dict[str, object]:
    aggregates = shape.mapping(value, "rollback aggregates")
    shape.exact_keys(aggregates, shape.AGGREGATE_KEYS, "rollback aggregates")
    _timestamp(aggregates["as_of_utc"])
    if aggregates["window_days"] != _AGGREGATE_WINDOW_DAYS:
        raise ValueError("rollback aggregate window must equal 30 days")
    for name in ("pending_stage_a", "pending_stage_b"):
        shape.integer(aggregates[name], f"rollback aggregates.{name}")
    for name in (
        "needs_attention_sha256",
        "funnel_sha256",
        "daily_cost_sha256",
        "llm_percentiles_sha256",
    ):
        shape.sha(aggregates[name], f"rollback aggregates.{name}")
    return aggregates


def _manifest_table_results(
    manifest: dict[str, object],
) -> tuple[TableVerificationResult, ...]:
    tables = shape.mapping(manifest["tables"], "cutover manifest tables")
    return tuple(
        _table_result(shape.mapping(tables[name], f"cutover {name}"), name)
        for name in MIGRATED_TABLE_ORDER_V1
    )


def _validated_result_tables(
    tables: tuple[TableVerificationResult, ...],
) -> tuple[TableVerificationResult, ...]:
    names = tuple(table.table_name for table in tables)
    if names != MIGRATED_TABLE_ORDER_V1:
        raise ValueError("cutover proof table order or coverage mismatch")
    for table in tables:
        shape.integer(table.row_count, f"cutover {table.table_name} row count")
        if table.max_identity is not None:
            shape.integer(
                table.max_identity, f"cutover {table.table_name} max identity"
            )
        shape.sha(table.canonical_sha256, f"cutover {table.table_name} SHA")
    return tables


def _table_result(
    document: dict[str, object], table_name: str
) -> TableVerificationResult:
    count = shape.integer(document["row_count"], f"{table_name} row count")
    maximum = document["max_identity"]
    if maximum is not None:
        maximum = shape.integer(maximum, f"{table_name} max identity")
    if table_name in _GENERATED_ID_TABLES and count and maximum is None:
        raise ValueError(f"rollback max identity missing: {table_name}")
    return TableVerificationResult(
        table_name=table_name,
        row_count=count,
        max_identity=maximum,
        canonical_sha256=shape.sha(document["canonical_sha256"], f"{table_name} SHA"),
    )


def _timestamp(value: object) -> datetime:
    text = shape.text(value, "rollback aggregate as_of_utc")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("rollback aggregate cutoff is not a timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("rollback aggregate cutoff must be timezone-aware")
    return parsed.astimezone(UTC)


def _document(value: object, name: str) -> dict[str, object]:
    normalized = (
        asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
    )
    if not isinstance(normalized, dict) or not all(
        isinstance(key, str) for key in normalized
    ):
        raise ValueError(f"{name} must be an object or dataclass")
    return cast(dict[str, object], normalized)
