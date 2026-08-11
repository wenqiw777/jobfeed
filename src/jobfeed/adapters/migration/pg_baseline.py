"""Canonical PostgreSQL-0008 baseline manifest and benchmark capture."""

from __future__ import annotations

import asyncio
import hashlib
import platform
from datetime import UTC, datetime
from typing import Final

from jobfeed.adapters.migration._baseline_workload import (
    _summary,
    artifact_sha256,
    validate_benchmark_workload,
)
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_benchmark_runner import (
    run_postgres_store_benchmarks,
)
from jobfeed.adapters.migration.canonical_row import CanonicalRowHasher
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
    canonical_schema_manifest_document,
    validate_schema_manifest,
)

_GENERATED_ID_TABLES = frozenset(
    {
        "jobs",
        "evaluations",
        "pipeline_runs",
        "job_status_history",
        "llm_usage",
        "interview_rounds",
        "step_timings",
    }
)
_ACTIVITY_COLUMNS: Final = {
    "jobs": ("discovered_at", "enriched_at", "closed_at"),
    "pipeline_runs": ("started_at", "finished_at"),
    "llm_usage": ("timestamp",),
    "step_timings": ("created_at",),
    "applied": ("applied_at",),
    "job_status_history": ("changed_at",),
    "interview_rounds": ("created_at", "scheduled_at", "completed_at"),
}


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


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return str(value)


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


def _table_metrics(
    reader: PostgresBaselineReader, chunk_size: int
) -> dict[str, object]:
    """Hash all registry rows.

    Time complexity is O(R), with O(chunk_size) database memory.
    """
    metrics: dict[str, object] = {}
    for table, schema in zip(
        CANONICAL_SCHEMA_MANIFEST_V1.tables,
        CANONICAL_ROW_SCHEMAS_V1,
        strict=True,
    ):
        hasher = CanonicalRowHasher(schema)
        count = 0
        for row in reader.stream_table(table.name, chunk_size):
            hasher.update_rows([row])
            count += 1
        max_identity = None
        if table.name in _GENERATED_ID_TABLES:
            max_identity = reader.scalar(f'SELECT MAX(id) FROM "{table.name}"')
        metrics[table.name] = {
            "row_count": count,
            "primary_key": list(table.primary_key),
            "max_identity": max_identity,
            "canonical_sha256": hasher.hexdigest(),
        }
    return metrics


def _activity_maxima(reader: PostgresBaselineReader) -> dict[str, object]:
    return {
        table: {
            column: _timestamp(reader.scalar(f'SELECT MAX("{column}") FROM "{table}"'))
            for column in columns
        }
        for table, columns in _ACTIVITY_COLUMNS.items()
    }


def capture_pg_baseline(
    dsn: str,
    workload_document: object,
    *,
    git_commit: str,
    chunk_size: int = 1000,
) -> tuple[dict[str, object], dict[str, object]]:
    """Capture one gated manifest and benchmark from a PostgreSQL-0008 snapshot.

    Args:
        dsn: PostgreSQL DSN from a named environment variable.
        workload_document: Parsed frozen benchmark workload.
        git_commit: Full source commit SHA.
        chunk_size: Server-side canonical hashing fetch size.

    Returns:
        Snapshot manifest and benchmark report.
    """
    workload = validate_benchmark_workload(workload_document)
    captured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with PostgresBaselineReader(dsn) as reader:
        revision, active_writers, running_runs = _gate_state(reader)
        validate_live_schema(reader.live_schema_document())
        manifest: dict[str, object] = {
            "format_version": 1,
            "created_at_utc": captured_at,
            "git_commit": git_commit,
            "schema_registry": canonical_schema_manifest_document(),
            "source": {
                "backend": "postgresql",
                "alembic_revision": revision,
                "server_version": reader.scalar("SHOW server_version"),
                "database_size_bytes": reader.scalar(
                    "SELECT pg_database_size(current_database())"
                ),
                "jobs_size_bytes": reader.scalar(
                    "SELECT pg_total_relation_size('jobs')"
                ),
            },
            "writer_quiescence": {
                "active_jobfeed_writers": active_writers,
                "historical_running_runs": running_runs,
            },
            "tables": _table_metrics(reader, chunk_size),
            "activity_maxima": _activity_maxima(reader),
        }
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
        contention_samples = reader.contention_samples(
            workload.contention.lock_key,
            workload.contention.hold_ms,
            workload.contention.samples,
        )
        _gate_state(reader)
    machine = "|".join((platform.system(), platform.release(), platform.machine()))
    benchmark: dict[str, object] = {
        "report_version": 1,
        "created_at_utc": captured_at,
        "git_commit": git_commit,
        "snapshot_manifest_sha256": artifact_sha256(manifest),
        "workload_sha256": artifact_sha256(workload_document),
        "machine_fingerprint": hashlib.sha256(machine.encode()).hexdigest(),
        "warmup_count": workload.warmup_count,
        "sample_count": workload.sample_count,
        "queries": query_reports,
        "contention": {
            "mode": workload.contention.mode,
            "clients": workload.contention.clients,
            "hold_ms": workload.contention.hold_ms,
            **_summary(contention_samples),
        },
    }
    return manifest, benchmark
