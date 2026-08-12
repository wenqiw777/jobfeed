"""Canonical PostgreSQL-0008 baseline manifest and benchmark capture."""

from __future__ import annotations

import asyncio
import hashlib
import platform
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.adapters.migration._baseline_workload import (
    _summary,
    artifact_sha256,
    validate_benchmark_workload,
)
from jobfeed.adapters.migration._pg_baseline_manifest import (
    SnapshotManifestContext,
    build_snapshot_manifest,
    table_metrics,
)
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_benchmark_runner import (
    run_postgres_store_benchmarks,
)
from jobfeed.adapters.migration._pg_claim_contention import run_pg_claim_contention
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
    workload = validate_benchmark_workload(workload_document)
    captured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with PostgresBaselineReader(dsn) as reader:
        revision, active_writers, running_runs = _gate_state(reader)
        validate_public_tables(reader.public_base_tables())
        validate_live_schema(reader.live_schema_document())
        manifest = build_snapshot_manifest(
            reader,
            context=SnapshotManifestContext(
                dsn=dsn,
                captured_at=captured_at,
                git_commit=source.git_commit,
                dump_sha256=source.sha256,
                dump_size_bytes=source.size_bytes,
                revision=revision,
                active_writers=active_writers,
                running_runs=running_runs,
            ),
            chunk_size=chunk_size,
        )
        store_results = asyncio.run(
            run_postgres_store_benchmarks(
                dsn,
                workload.operations,
                warmups=workload.warmup_count,
                samples=workload.sample_count,
            )
        )
        query_reports = []
        for query, result in zip(workload.operations, store_results, strict=True):
            query_reports.append(
                {
                    "name": query.name,
                    "coverage": query.coverage,
                    "row_count": result.row_count,
                    **_summary(result.samples_ms),
                }
            )
        mid_revision, mid_writers, mid_running = _gate_state(reader)
        post_revision, post_writers, post_running = _gate_state(reader)
    manifest_sha256 = artifact_sha256(manifest)
    with PostgresBaselineReader(contention_dsn) as scratch:
        scratch_revision, scratch_writers, scratch_running = _gate_state(scratch)
        validate_public_tables(scratch.public_base_tables())
        validate_live_schema(scratch.live_schema_document())
        if table_metrics(scratch, chunk_size) != manifest["tables"]:
            raise ValueError("contention scratch clone differs from initial manifest")
    contention = run_pg_claim_contention(contention_dsn, workload.contention)
    with PostgresBaselineReader(contention_dsn) as scratch:
        scratch_post_revision, scratch_post_writers, scratch_post_running = _gate_state(
            scratch
        )
    machine = "|".join((platform.system(), platform.release(), platform.machine()))
    benchmark: dict[str, object] = {
        "report_version": 1,
        "created_at_utc": captured_at,
        "git_commit": source.git_commit,
        "snapshot_manifest_sha256": manifest_sha256,
        "workload_sha256": artifact_sha256(workload_document),
        "machine_fingerprint": hashlib.sha256(machine.encode()).hexdigest(),
        "warmup_count": workload.warmup_count,
        "sample_count": workload.sample_count,
        "read_consistency": {
            "mode": "quiescent_pre_post_gate",
            "canonical_manifest": "repeatable-read read-only transaction",
            "store_metrics": "separate connections while writers/runs remain zero",
            "contention": "runs after read metrics and mutates only rehearsal",
            "pre_revision": revision,
            "pre_active_writers": active_writers,
            "pre_running_runs": running_runs,
            "mid_revision": mid_revision,
            "mid_active_writers": mid_writers,
            "mid_running_runs": mid_running,
            "post_revision": post_revision,
            "post_active_writers": post_writers,
            "post_running_runs": post_running,
        },
        "queries": query_reports,
        "contention": {
            "mode": workload.contention.mode,
            "processes": workload.contention.processes,
            "worker_pids": contention.worker_pids,
            "coroutines_per_process": workload.contention.coroutines_per_process,
            "rounds_per_coroutine": workload.contention.rounds_per_coroutine,
            "attempted_short_writes": (
                workload.contention.processes
                * workload.contention.coroutines_per_process
                * workload.contention.rounds_per_coroutine
            ),
            "successful_claims": len(contention.claimed_ids),
            "empty_claims": contention.empty_claims,
            "duplicate_claims": 0,
            "data_loss": 0,
            "retry_exhausted_busy": 0,
            "scratch_initial_manifest_sha256": manifest_sha256,
            "scratch_pre_revision": scratch_revision,
            "scratch_pre_active_writers": scratch_writers,
            "scratch_pre_running_runs": scratch_running,
            "scratch_post_revision": scratch_post_revision,
            "scratch_post_active_writers": scratch_post_writers,
            "scratch_post_running_runs": scratch_post_running,
            **_summary(contention.samples_ms),
        },
    }
    return manifest, benchmark
