"""Canonical PostgreSQL-0008 baseline manifest and benchmark capture."""

from __future__ import annotations

import asyncio
import platform
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.adapters.migration._baseline_machine import component_fingerprints
from jobfeed.adapters.migration._baseline_workload import (
    artifact_sha256,
    validate_benchmark_workload,
)
from jobfeed.adapters.migration._pg_baseline_manifest import (
    SnapshotManifestContext,
    build_snapshot_manifest,
    table_metrics,
)
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_baseline_report import (
    ReportContext,
    build_benchmark_report,
)
from jobfeed.adapters.migration._pg_benchmark_merge import merge_benchmark_results
from jobfeed.adapters.migration._pg_benchmark_runner import (
    run_postgres_store_benchmarks,
)
from jobfeed.adapters.migration._pg_claim_contention import (
    run_pg_claim_contention,
    validate_claim_contention_outcome,
)
from jobfeed.adapters.migration._pg_scratch_mutations import (
    ScratchMutationConfig,
    ScratchMutationTarget,
)
from jobfeed.adapters.migration._pg_scratch_runner import (
    run_pg_scratch_mutation_benchmarks,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    validate_schema_manifest,
)

_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, kw_only=True)
class PgDumpEvidence:
    """Immutable identity and code provenance for one restored pg_dump."""

    git_commit: str
    sha256: str
    size_bytes: int
    restore_attestations: dict[str, object]
    machine_token: str


def assert_capture_allowed(
    *, revision: str, active_writers: int, running_runs: int
) -> None:
    """Reject a non-0008 or non-quiescent PostgreSQL source.

    Args:
        revision: Live Alembic revision.
        active_writers: Conservative count of other non-idle client sessions.
        running_runs: Persisted running pipeline count.

    Raises:
        ValueError: If any baseline precondition is false.
    """
    if revision != "0008":
        raise ValueError(f"baseline requires Alembic 0008, got {revision}")
    if active_writers:
        raise ValueError(f"baseline source has {active_writers} active writer sessions")
    if running_runs:
        raise ValueError(f"baseline source has {running_runs} running pipeline runs")


def validate_live_schema(document: object) -> None:
    """Require a live schema document to equal the frozen 14-table registry.

    Args:
        document: Registry-shaped live information_schema evidence.

    Raises:
        ValueError: If any schema field differs.
    """
    if not isinstance(document, dict):
        raise ValueError("live schema mismatch: expected object")
    validate_schema_manifest(document)


def validate_public_tables(table_names: list[str]) -> None:
    """Require exactly 14 migrated tables plus Alembic metadata.

    Args:
        table_names: Complete live public base-table names.

    Raises:
        ValueError: If a table is missing, extra, or duplicated.
    """
    expected = {table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables}
    expected.add("alembic_version")
    actual = set(table_names)
    if len(actual) != len(table_names) or actual != expected:
        raise ValueError(
            "live public table mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _as_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError(f"expected PostgreSQL integer scalar, got {value!r}")
    return value


def _gate_state(reader: PostgresBaselineReader) -> tuple[str, int, int]:
    revision = str(reader.scalar("SELECT version_num FROM alembic_version"))
    active_writers = _as_int(
        reader.scalar(
            "SELECT COUNT(*) FROM pg_stat_activity "
            "WHERE datname=current_database() AND pid<>pg_backend_pid() "
            "AND backend_type='client backend' AND state<>'idle'"
        )
    )
    running_runs = _as_int(
        reader.scalar("SELECT COUNT(*) FROM pipeline_runs WHERE status='running'")
    )
    assert_capture_allowed(
        revision=revision,
        active_writers=active_writers,
        running_runs=running_runs,
    )
    return revision, active_writers, running_runs


def capture_pg_baseline(
    dsn: str,
    contention_dsn: str,
    workload_document: object,
    *,
    source: PgDumpEvidence,
    chunk_size: int = 1000,
) -> tuple[dict[str, object], dict[str, object]]:
    """Capture one gated manifest and benchmark from a PostgreSQL-0008 snapshot.

    Args:
        dsn: PostgreSQL DSN from a named environment variable.
        contention_dsn: Separate disposable clone restored from the same dump.
        workload_document: Parsed frozen benchmark workload.
        source: Dump identity plus full source commit SHA.
        chunk_size: Server-side canonical hashing fetch size.

    Returns:
        Snapshot manifest and benchmark report.

    Raises:
        ValueError: If source, schema, quiescence, or benchmark gates fail.
    """
    if len(source.sha256) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in source.sha256
    ):
        raise ValueError("source pg_dump SHA-256 must be lowercase hexadecimal")
    if source.size_bytes <= 0:
        raise ValueError("source pg_dump must be non-empty")
    return _capture_validated_baseline(
        dsn, contention_dsn, workload_document, source=source, chunk_size=chunk_size
    )


def _capture_validated_baseline(
    dsn: str,
    contention_dsn: str,
    workload_document: object,
    *,
    source: PgDumpEvidence,
    chunk_size: int,
) -> tuple[dict[str, object], dict[str, object]]:
    workload = validate_benchmark_workload(workload_document)
    captured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    source_attestation = source.restore_attestations.get("source")
    scratch_attestation = source.restore_attestations.get("scratch")
    if not isinstance(source_attestation, dict) or not isinstance(
        scratch_attestation, dict
    ):
        raise ValueError("validated source and scratch attestations are required")
    with PostgresBaselineReader(dsn) as reader:
        source_gate = _gate_state(reader)
        validate_public_tables(reader.public_base_tables())
        validate_live_schema(reader.live_schema_document())
        source_identity = reader.database_identity()
        if source_identity != source_attestation.get("database_identity"):
            raise ValueError("source database identity differs from attestation")
        manifest = build_snapshot_manifest(
            reader,
            context=SnapshotManifestContext(
                dsn=dsn,
                captured_at=captured_at,
                git_commit=source.git_commit,
                dump_sha256=source.sha256,
                dump_size_bytes=source.size_bytes,
                revision=source_gate[0],
                active_writers=source_gate[1],
                running_runs=source_gate[2],
                restore_attestations=source.restore_attestations,
            ),
            chunk_size=chunk_size,
        )
    read_operations = tuple(
        operation
        for operation in workload.operations
        if not operation.coverage.startswith("overhead.")
    )
    read_results = asyncio.run(
        run_postgres_store_benchmarks(
            dsn,
            read_operations,
            warmups=workload.warmup_count,
            samples=workload.sample_count,
        )
    )
    with PostgresBaselineReader(dsn) as fresh_reader:
        source_post_gate = _gate_state(fresh_reader)
        validate_public_tables(fresh_reader.public_base_tables())
        validate_live_schema(fresh_reader.live_schema_document())
        if fresh_reader.database_identity() != source_identity:
            raise ValueError("source database identity changed during benchmark")
        if table_metrics(fresh_reader, chunk_size) != manifest["tables"]:
            raise ValueError("source data changed during store read benchmark")
    manifest_sha256 = artifact_sha256(manifest)
    with PostgresBaselineReader(contention_dsn) as scratch:
        scratch_gate = _gate_state(scratch)
        validate_public_tables(scratch.public_base_tables())
        validate_live_schema(scratch.live_schema_document())
        scratch_identity = scratch.database_identity()
        if scratch_identity != scratch_attestation.get("database_identity"):
            raise ValueError("scratch database identity differs from attestation")
        if scratch_identity == source_identity:
            raise ValueError("source and scratch database identities must differ")
        if table_metrics(scratch, chunk_size) != manifest["tables"]:
            raise ValueError("contention scratch clone differs from initial manifest")
        claim_cutoff = scratch.database_clock()
    contention = run_pg_claim_contention(contention_dsn, workload.contention)
    with PostgresBaselineReader(contention_dsn) as scratch:
        scratch_post_gate = _gate_state(scratch)
        persisted_claim_ids = scratch.stage_a_claimed_ids_since(claim_cutoff)
    validate_claim_contention_outcome(
        claimed_by_process=contention.claimed_by_process,
        errors=contention.errors,
        persisted_claim_ids=persisted_claim_ids,
    )
    scratch_mutations = run_pg_scratch_mutation_benchmarks(
        ScratchMutationTarget(
            dsn=contention_dsn,
            expected_database_identity=scratch_identity,
            source_database_identity=source_identity,
        ),
        ScratchMutationConfig(
            fixture_prefix=f"baseline-{manifest_sha256[:16]}",
            warmup_count=workload.warmup_count,
            sample_count=workload.sample_count,
        ),
    )
    store_results = merge_benchmark_results(
        workload.operations, read_results, scratch_mutations
    )
    with PostgresBaselineReader(contention_dsn) as scratch:
        scratch_post_gate = _gate_state(scratch)
        if scratch.database_identity() != scratch_identity:
            raise ValueError("scratch database identity changed during benchmark")
    cpu_identifier = platform.processor() or platform.machine()
    machine, machine_token_hash, cpu_hash = component_fingerprints(
        source.machine_token, cpu_identifier
    )
    benchmark = build_benchmark_report(
        ReportContext(
            captured_at=captured_at,
            git_commit=source.git_commit,
            manifest_sha256=manifest_sha256,
            workload_document=workload_document,
            workload=workload,
            store_results=store_results,
            machine_fingerprint=machine,
            machine_token_sha256=machine_token_hash,
            cpu_identifier_sha256=cpu_hash,
            source_gate=source_gate,
            source_post_gate=source_post_gate,
            scratch_gate=scratch_gate,
            scratch_post_gate=scratch_post_gate,
            contention=contention,
            persisted_claim_ids=persisted_claim_ids,
            scratch_mutations=scratch_mutations,
        )
    )
    return manifest, benchmark
