"""Exact, acyclic baseline evidence and restore-attestation contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from jobfeed.adapters.migration import _baseline_evidence_shape as shape
from jobfeed.adapters.migration._baseline_evidence_benchmark import (
    validate_benchmark_document,
)
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    validate_schema_manifest,
)

_ATTESTATION_KEYS = {
    "attestation_version",
    "dump_sha256",
    "container_id",
    "database_identity",
    "restore_tool",
    "restore_tool_version",
    "restore_command_sha256",
    "pre_upgrade_revision",
    "post_upgrade_revision",
}
_AGGREGATE_WINDOW_DAYS = 30


@dataclass(frozen=True, kw_only=True)
class RestoreAttestation:
    """Orchestrator evidence for one dump restore and 0008 upgrade."""

    attestation_version: int
    dump_sha256: str
    container_id: str
    database_identity: str
    restore_tool: str
    restore_tool_version: str
    restore_command_sha256: str
    pre_upgrade_revision: str
    post_upgrade_revision: str


def _restore_attestation(value: object, name: str) -> RestoreAttestation:
    document = shape.mapping(value, name)
    shape.exact_keys(document, _ATTESTATION_KEYS, name)
    if document["attestation_version"] != 1:
        raise ValueError(f"{name} unknown attestation version")
    if document["pre_upgrade_revision"] != "0007":
        raise ValueError(f"{name} must attest pre-upgrade revision 0007")
    if document["post_upgrade_revision"] != "0008":
        raise ValueError(f"{name} must attest post-upgrade revision 0008")
    return RestoreAttestation(
        attestation_version=1,
        dump_sha256=shape.sha(document["dump_sha256"], f"{name}.dump_sha256"),
        container_id=shape.text(document["container_id"], f"{name}.container_id"),
        database_identity=shape.sha(
            document["database_identity"], f"{name}.database_identity"
        ),
        restore_tool=shape.text(document["restore_tool"], f"{name}.restore_tool"),
        restore_tool_version=shape.text(
            document["restore_tool_version"], f"{name}.restore_tool_version"
        ),
        restore_command_sha256=shape.sha(
            document["restore_command_sha256"], f"{name}.restore_command_sha256"
        ),
        pre_upgrade_revision="0007",
        post_upgrade_revision="0008",
    )


def validate_restore_attestations(
    source: object, scratch: object, *, dump_sha256: str
) -> dict[str, object]:
    """Validate two distinct restores of the exact named dump.

    Args:
        source: Immutable read/manifest database restore attestation.
        scratch: Disposable contention database restore attestation.
        dump_sha256: Independently hashed dump artifact.

    Returns:
        Canonical source/scratch attestation document.

    Raises:
        ValueError: If provenance is incomplete, inconsistent, or not distinct.
    """
    expected_dump = shape.sha(dump_sha256, "dump_sha256")
    source_value = _restore_attestation(source, "source attestation")
    scratch_value = _restore_attestation(scratch, "scratch attestation")
    if (
        source_value.dump_sha256 != expected_dump
        or scratch_value.dump_sha256 != expected_dump
    ):
        raise ValueError("restore attestations do not bind the selected dump")
    if source_value.container_id == scratch_value.container_id:
        raise ValueError("restore attestations require distinct containers")
    if source_value.database_identity == scratch_value.database_identity:
        raise ValueError("restore attestations require distinct database identities")
    return {"source": asdict(source_value), "scratch": asdict(scratch_value)}


def _validate_manifest_tables(manifest_doc: dict[str, object]) -> None:
    validate_schema_manifest(
        shape.mapping(manifest_doc["schema_registry"], "schema registry")
    )
    tables = shape.mapping(manifest_doc["tables"], "manifest.tables")
    expected_tables = [table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables]
    if list(tables) != expected_tables:
        raise ValueError("manifest table order/coverage mismatch")
    for table in CANONICAL_SCHEMA_MANIFEST_V1.tables:
        metric = shape.mapping(tables[table.name], f"manifest.tables.{table.name}")
        shape.exact_keys(
            metric, shape.TABLE_METRIC_KEYS, f"manifest.tables.{table.name}"
        )
        if metric["primary_key"] != list(table.primary_key):
            raise ValueError(f"manifest table primary key mismatch: {table.name}")
        row_count = shape.integer(
            metric["row_count"], f"manifest table row count: {table.name}"
        )
        if table.name == "jobs" and row_count == 0:
            raise ValueError("manifest requires non-empty jobs seed data")
        max_identity = metric["max_identity"]
        if max_identity is not None:
            shape.integer(max_identity, f"manifest max identity: {table.name}")
        shape.sha(metric["canonical_sha256"], f"manifest table SHA: {table.name}")


def _validate_source(manifest_doc: dict[str, object]) -> str:
    source = shape.mapping(manifest_doc["source"], "manifest.source")
    shape.exact_keys(source, shape.SOURCE_KEYS, "manifest.source")
    dump_sha = shape.sha(source.get("source_dump_sha256"), "source dump")
    if source["backend"] != "postgresql" or source["alembic_revision"] != "0008":
        raise ValueError("manifest source backend/revision mismatch")
    if source["consistent_snapshot_id"] != f"pgdump-sha256:{dump_sha}":
        raise ValueError("manifest restored dump identity mismatch")
    shape.text(source["server_version"], "manifest source server version")
    for key in ("source_dump_size_bytes", "database_size_bytes", "jobs_size_bytes"):
        shape.integer(source[key], f"manifest.source.{key}", minimum=1)
    return dump_sha


def _validate_aggregate_target(manifest_doc: dict[str, object]) -> None:
    aggregates = shape.mapping(manifest_doc["aggregates"], "manifest.aggregates")
    shape.exact_keys(aggregates, shape.AGGREGATE_KEYS, "manifest.aggregates")
    for key in (
        "needs_attention_sha256",
        "funnel_sha256",
        "daily_cost_sha256",
        "llm_percentiles_sha256",
    ):
        shape.sha(aggregates[key], f"manifest.aggregates.{key}")
    shape.text(aggregates["as_of_utc"], "manifest.aggregates.as_of_utc")
    if aggregates["window_days"] != _AGGREGATE_WINDOW_DAYS:
        raise ValueError("manifest aggregate window must equal 30 days")
    for key in ("pending_stage_a", "pending_stage_b"):
        shape.integer(aggregates[key], f"manifest.aggregates.{key}")
    target = shape.mapping(manifest_doc["target"], "manifest.target")
    shape.exact_keys(target, shape.TARGET_KEYS, "manifest.target")
    if target != {
        "status": "not_applicable_postgres_baseline",
        "backend": "sqlite",
        "sqlite_schema_version": 1,
        "minimum_sqlite_version": "3.35.0",
        "migrated_table_count": 14,
        "total_table_count": 15,
        "sqlite_file_sha256": None,
    }:
        raise ValueError("manifest target placeholder mismatch")


def _validate_manifest(manifest_doc: dict[str, object]) -> str:
    """Validate manifest provenance and nested evidence.

    Time complexity is O(tables + fields).
    """
    shape.exact_keys(manifest_doc, shape.MANIFEST_KEYS, "manifest")
    if manifest_doc["format_version"] != 1:
        raise ValueError("unknown manifest version")
    shape.text(manifest_doc["created_at_utc"], "manifest.created_at_utc")
    shape.text(manifest_doc["git_commit"], "manifest.git_commit")
    dump_sha = _validate_source(manifest_doc)
    _validate_manifest_tables(manifest_doc)
    _validate_aggregate_target(manifest_doc)
    attestations = shape.mapping(
        manifest_doc["restore_attestations"], "restore attestations"
    )
    shape.exact_keys(attestations, {"source", "scratch"}, "restore attestations")
    validate_restore_attestations(
        attestations["source"], attestations["scratch"], dump_sha256=dump_sha
    )
    quiescence = shape.mapping(
        manifest_doc["writer_quiescence"], "manifest.writer_quiescence"
    )
    shape.exact_keys(quiescence, shape.QUIESCENCE_KEYS, "manifest.writer_quiescence")
    if (
        quiescence["active_jobfeed_writers"] != 0
        or quiescence["historical_running_runs"] != 0
    ):
        raise ValueError("manifest writer quiescence mismatch")
    shape.text(quiescence["checked_at_utc"], "manifest quiescence checked_at")
    activity = shape.mapping(
        manifest_doc["activity_maxima"], "manifest.activity_maxima"
    )
    if set(activity) != set(shape.ACTIVITY_COLUMNS):
        raise ValueError("manifest activity maxima coverage mismatch")
    for table, columns in shape.ACTIVITY_COLUMNS.items():
        maxima = shape.mapping(activity[table], f"manifest.activity_maxima.{table}")
        shape.exact_keys(maxima, columns, f"manifest activity maxima {table}")
        for column, value in maxima.items():
            shape.optional_text(value, f"manifest activity maximum {table}.{column}")
    return dump_sha


def validate_evidence_bundle(
    manifest: object,
    benchmark: object,
    index: object,
    *,
    verify_hashes: bool = True,
) -> None:
    """Validate exact artifact schemas and one-way manifest-to-benchmark hashes.

    Args:
        manifest: Independent dump/schema/data artifact.
        benchmark: Artifact that binds the manifest and workload hashes.
        index: Final artifact that binds both prior artifacts.
        verify_hashes: Whether to recompute manifest and benchmark SHA values.

    Raises:
        ValueError: If schemas, versions, or acyclic hash links differ.
    """
    manifest_doc = shape.mapping(manifest, "manifest")
    benchmark_doc = shape.mapping(benchmark, "benchmark")
    index_doc = shape.mapping(index, "evidence index")
    dump_sha = _validate_manifest(manifest_doc)
    validate_benchmark_document(benchmark_doc)
    shape.exact_keys(index_doc, shape.INDEX_KEYS, "evidence index")
    if index_doc["evidence_version"] != 1:
        raise ValueError("unknown evidence index version")
    if (
        benchmark_doc["git_commit"] != manifest_doc["git_commit"]
        or index_doc["git_commit"] != manifest_doc["git_commit"]
    ):
        raise ValueError("evidence git commit mismatch")
    if benchmark_doc["created_at_utc"] != manifest_doc["created_at_utc"]:
        raise ValueError("evidence capture time mismatch")
    if index_doc["source_dump_sha256"] != dump_sha:
        raise ValueError("evidence index dump SHA mismatch")
    if benchmark_doc["workload_sha256"] != index_doc["workload_sha256"]:
        raise ValueError("benchmark workload SHA mismatch")
    if verify_hashes:
        manifest_sha = artifact_sha256(manifest_doc)
        benchmark_sha = artifact_sha256(benchmark_doc)
        if benchmark_doc["snapshot_manifest_sha256"] != manifest_sha:
            raise ValueError("benchmark manifest SHA mismatch")
        if index_doc["manifest_sha256"] != manifest_sha:
            raise ValueError("evidence index manifest SHA mismatch")
        if index_doc["benchmark_sha256"] != benchmark_sha:
            raise ValueError("evidence index benchmark SHA mismatch")
