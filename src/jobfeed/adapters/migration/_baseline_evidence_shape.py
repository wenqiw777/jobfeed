"""Constants and primitive checks for exact baseline evidence schemas."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

HEX_LENGTH = 64
MANIFEST_KEYS = {
    "format_version",
    "created_at_utc",
    "git_commit",
    "schema_registry",
    "source",
    "restore_attestations",
    "writer_quiescence",
    "tables",
    "activity_maxima",
    "aggregates",
    "target",
}
BENCHMARK_KEYS = {
    "report_version",
    "created_at_utc",
    "git_commit",
    "snapshot_manifest_sha256",
    "workload_sha256",
    "machine_fingerprint",
    "machine_token_sha256",
    "cpu_identifier_sha256",
    "warmup_count",
    "sample_count",
    "read_consistency",
    "queries",
    "contention",
    "scratch_mutations",
}
INDEX_KEYS = {
    "evidence_version",
    "source_dump_sha256",
    "manifest_sha256",
    "benchmark_sha256",
    "workload_sha256",
    "git_commit",
}
SOURCE_KEYS = {
    "backend",
    "alembic_revision",
    "source_dump_sha256",
    "source_dump_size_bytes",
    "consistent_snapshot_id",
    "server_version",
    "database_size_bytes",
    "jobs_size_bytes",
}
TABLE_METRIC_KEYS = {"row_count", "primary_key", "max_identity", "canonical_sha256"}
AGGREGATE_KEYS = {
    "as_of_utc",
    "window_days",
    "pending_stage_a",
    "pending_stage_b",
    "needs_attention_sha256",
    "funnel_sha256",
    "daily_cost_sha256",
    "llm_percentiles_sha256",
}
TARGET_KEYS = {
    "status",
    "backend",
    "sqlite_schema_version",
    "minimum_sqlite_version",
    "migrated_table_count",
    "total_table_count",
    "sqlite_file_sha256",
}
QUERY_KEYS = {"name", "coverage", "row_count", "p50_ms", "p95_ms", "max_ms"}
QUIESCENCE_KEYS = {
    "checked_at_utc",
    "active_jobfeed_writers",
    "historical_running_runs",
}
READ_CONSISTENCY_KEYS = {
    "mode",
    "canonical_manifest",
    "store_metrics",
    "contention",
    "pre_revision",
    "pre_active_writers",
    "pre_running_runs",
    "post_revision",
    "post_active_writers",
    "post_running_runs",
}
CONTENTION_KEYS = {
    "mode",
    "processes",
    "worker_pids",
    "successful_claims_by_process",
    "coroutines_per_process",
    "rounds_per_coroutine",
    "attempted_short_writes",
    "successful_claims",
    "database_claim_count",
    "database_claim_ids_sha256",
    "empty_claims",
    "duplicate_claims",
    "data_loss",
    "retry_exhausted_busy",
    "scratch_initial_manifest_sha256",
    "scratch_pre_revision",
    "scratch_pre_active_writers",
    "scratch_pre_running_runs",
    "scratch_post_revision",
    "scratch_post_active_writers",
    "scratch_post_running_runs",
    "p50_ms",
    "p95_ms",
    "max_ms",
}
SCRATCH_MUTATION_KEYS = {
    "mode",
    "setup_in_timed_samples",
    "sample_count",
    "scan",
    "evaluate",
}
SCAN_MUTATION_KEYS = {
    "operation",
    "verified_rows",
    "p50_ms",
    "p95_ms",
    "max_ms",
}
EVALUATE_MUTATION_KEYS = {*SCAN_MUTATION_KEYS, "paths"}
PATH_MUTATION_KEYS = {"sample_count", "p50_ms", "p95_ms", "max_ms"}
EVALUATE_PATHS = {"claim_release", "claim_result", "claim_error"}
ACTIVITY_COLUMNS = {
    "jobs": {"discovered_at", "enriched_at", "closed_at"},
    "pipeline_runs": {"started_at", "finished_at"},
    "llm_usage": {"timestamp"},
    "step_timings": {"created_at"},
    "applied": {"applied_at"},
    "job_status_history": {"changed_at"},
    "interview_rounds": {"created_at", "scheduled_at", "completed_at"},
}


def mapping(value: object, name: str) -> dict[str, object]:
    """Require a string-keyed object.

    Args:
        value: Candidate object.
        name: Error path.

    Returns:
        Typed mapping.

    Raises:
        ValueError: If the candidate is not a string-keyed object.
    """
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def exact_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    """Require exactly one named key set.

    Args:
        value: Mapping to inspect.
        expected: Required keys.
        name: Error path.

    Raises:
        ValueError: If any key is missing or extra.
    """
    if set(value) != expected:
        raise ValueError(
            f"{name} exact keys mismatch: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def text(value: object, name: str) -> str:
    """Require non-empty text.

    Args:
        value: Candidate text.
        name: Error path.

    Returns:
        Validated text.

    Raises:
        ValueError: If the candidate is empty or not text.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def sha(value: object, name: str) -> str:
    """Require lowercase SHA-256 text.

    Args:
        value: Candidate digest.
        name: Error path.

    Returns:
        Validated digest.

    Raises:
        ValueError: If the candidate is not lowercase SHA-256 text.
    """
    digest = text(value, name)
    if len(digest) != HEX_LENGTH or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return digest


def integer(value: object, name: str, *, minimum: int = 0) -> int:
    """Require a non-boolean integer at or above a lower bound.

    Args:
        value: Candidate integer.
        name: Error path.
        minimum: Inclusive lower bound.

    Returns:
        Validated integer.

    Raises:
        ValueError: If the value is not an integer at or above the bound.
    """
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def number(value: object, name: str, *, minimum: float = 0.0) -> float:
    """Require one finite non-boolean numeric measurement.

    Args:
        value: Candidate measurement.
        name: Error path.
        minimum: Inclusive lower bound.

    Returns:
        Validated floating representation.

    Raises:
        ValueError: If the value is boolean, nonnumeric, nonfinite, or too low.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return numeric


def optional_text(value: object, name: str) -> str | None:
    """Require either non-empty text or JSON null.

    Args:
        value: Candidate optional text.
        name: Error path.

    Returns:
        Validated text or None.
    """
    if value is None:
        return None
    return text(value, name)


def timing_summary(value: Mapping[str, object], name: str) -> None:
    """Require finite nonnegative p50 <= p95 <= max timing metrics.

    Args:
        value: Mapping containing all three timing fields.
        name: Error path.

    Raises:
        ValueError: If a metric is invalid or percentile order is impossible.
    """
    p50 = number(value["p50_ms"], f"{name}.p50_ms")
    p95 = number(value["p95_ms"], f"{name}.p95_ms")
    maximum = number(value["max_ms"], f"{name}.max_ms")
    if not p50 <= p95 <= maximum:
        raise ValueError(f"{name} timing order must satisfy p50 <= p95 <= max")
