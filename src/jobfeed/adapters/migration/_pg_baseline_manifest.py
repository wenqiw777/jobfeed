"""PostgreSQL snapshot manifest construction helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Final

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_canonical_aggregates import (
    capture_canonical_aggregates,
)
from jobfeed.adapters.migration.canonical_row import CanonicalRowHasher
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
    canonical_schema_manifest_document,
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


@dataclass(frozen=True, kw_only=True)
class SnapshotManifestContext:
    """Immutable provenance and gate values for one snapshot manifest."""

    dsn: str
    captured_at: str
    git_commit: str
    dump_sha256: str
    dump_size_bytes: int
    revision: str
    active_writers: int
    running_runs: int
    restore_attestations: dict[str, object]


def timestamp_value(value: object) -> str | None:
    """Normalize a timestamp-like scalar for JSON evidence.

    Args:
        value: PostgreSQL scalar.

    Returns:
        UTC timestamp text, raw text, or None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return str(value)


def table_metrics(reader: PostgresBaselineReader, chunk_size: int) -> dict[str, object]:
    """Hash all registry rows.

    Time complexity is O(R), with O(chunk_size) database memory.

    Args:
        reader: Active repeatable-read PostgreSQL snapshot reader.
        chunk_size: Maximum server-side rows fetched per chunk.

    Returns:
        Per-table row counts, primary keys, identity maxima, and checksums.
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


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return timestamp_value(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _unordered_buckets(value: object) -> object:
    normalized = _json_value(value)
    if not isinstance(normalized, dict):
        raise ValueError("needs_attention aggregate must be an object")
    return {
        key: sorted(
            rows,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )
        for key, rows in sorted(normalized.items())
        if isinstance(rows, list)
    }


def aggregate_manifest(raw: dict[str, object]) -> dict[str, object]:
    """Hash unordered aggregate outputs after stable recursive sorting.

    Args:
        raw: Exact store aggregate outputs.

    Returns:
        Counts and backend-order-independent aggregate hashes.
    """
    return {
        "as_of_utc": timestamp_value(raw["as_of_utc"]),
        "window_days": raw["window_days"],
        "pending_stage_a": raw["pending_stage_a"],
        "pending_stage_b": raw["pending_stage_b"],
        "needs_attention_sha256": artifact_sha256(
            _unordered_buckets(raw["needs_attention"])
        ),
        "funnel_sha256": artifact_sha256(_json_value(raw["funnel"])),
        "daily_cost_sha256": artifact_sha256(_json_value(raw["daily_cost"])),
        "llm_percentiles_sha256": artifact_sha256(_json_value(raw["llm_percentiles"])),
    }


def build_snapshot_manifest(
    reader: PostgresBaselineReader,
    *,
    context: SnapshotManifestContext,
    chunk_size: int,
) -> dict[str, object]:
    """Build the immutable dump-bound snapshot manifest.

    Args:
        reader: Active repeatable-read PostgreSQL reader.
        context: Dump provenance, timestamp, and quiescence gate values.
        chunk_size: Canonical row fetch size.

    Returns:
        Independent manifest with no benchmark back-reference.
    """
    return {
        "format_version": 1,
        "created_at_utc": context.captured_at,
        "git_commit": context.git_commit,
        "schema_registry": canonical_schema_manifest_document(),
        "source": {
            "backend": "postgresql",
            "alembic_revision": context.revision,
            "source_dump_sha256": context.dump_sha256,
            "source_dump_size_bytes": context.dump_size_bytes,
            "consistent_snapshot_id": f"pgdump-sha256:{context.dump_sha256}",
            "server_version": reader.scalar("SHOW server_version"),
            "database_size_bytes": reader.scalar(
                "SELECT pg_database_size(current_database())"
            ),
            "jobs_size_bytes": reader.scalar("SELECT pg_total_relation_size('jobs')"),
        },
        "restore_attestations": context.restore_attestations,
        "writer_quiescence": {
            "checked_at_utc": context.captured_at,
            "active_jobfeed_writers": context.active_writers,
            "historical_running_runs": context.running_runs,
        },
        "tables": table_metrics(reader, chunk_size),
        "activity_maxima": {
            table: {
                column: timestamp_value(
                    reader.scalar(f'SELECT MAX("{column}") FROM "{table}"')
                )
                for column in columns
            }
            for table, columns in _ACTIVITY_COLUMNS.items()
        },
        "aggregates": aggregate_manifest(
            capture_canonical_aggregates(reader, reader.database_clock())
        ),
        "target": {
            "status": "not_applicable_postgres_baseline",
            "backend": "sqlite",
            "sqlite_schema_version": 1,
            "minimum_sqlite_version": "3.35.0",
            "migrated_table_count": 14,
            "total_table_count": 15,
            "sqlite_file_sha256": None,
        },
    }
