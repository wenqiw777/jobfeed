"""Validation and summaries for the frozen store benchmark workload."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

REQUIRED_BENCHMARK_COVERAGE: Final = frozenset(
    {
        "hot.list",
        "hot.detail",
        "hot.status",
        "views.query_jobs",
        "views.twin_rows",
        "views.twin_statuses",
        "views.pipeline_runs",
        "views.insights",
        "perf.overview",
        "perf.step_timings",
        "perf.llm_daily",
        "perf.funnel",
        "overhead.scan",
        "overhead.evaluate",
    }
)
_OPERATION_COVERAGE: Final = {
    "jobs_view_list": "hot.list",
    "job_detail": "hot.detail",
    "status_queue": "hot.status",
    "query_jobs_view": "views.query_jobs",
    "list_twin_rows_by_status": "views.twin_rows",
    "list_twin_statuses": "views.twin_statuses",
    "list_pipeline_runs": "views.pipeline_runs",
    "insights_overview": "views.insights",
    "get_performance_overview": "perf.overview",
    "get_step_timings": "perf.step_timings",
    "get_llm_daily_stats": "perf.llm_daily",
    "get_funnel_stats": "perf.funnel",
    "scan_upsert_lookup": "overhead.scan",
    "evaluate_pending_claim_candidates": "overhead.evaluate",
}
_OPERATION_PARAMS: Final = {
    "jobs_view_list": frozenset({"limit"}),
    "job_detail": frozenset(),
    "status_queue": frozenset({"limit"}),
    "query_jobs_view": frozenset({"limit"}),
    "list_twin_rows_by_status": frozenset({"limit"}),
    "list_twin_statuses": frozenset(),
    "list_pipeline_runs": frozenset({"limit"}),
    "insights_overview": frozenset({"window_days"}),
    "get_performance_overview": frozenset({"window_days"}),
    "get_step_timings": frozenset({"window_days"}),
    "get_llm_daily_stats": frozenset({"window_days"}),
    "get_funnel_stats": frozenset({"window_days"}),
    "scan_upsert_lookup": frozenset({"limit"}),
    "evaluate_pending_claim_candidates": frozenset({"limit"}),
}
_CONTENTION_CLIENTS = 2


@dataclass(frozen=True, kw_only=True)
class BenchmarkQuery:
    """One backend-neutral typed store operation descriptor."""

    name: str
    coverage: str
    operation: str
    params: Mapping[str, int]


@dataclass(frozen=True, kw_only=True)
class ContentionWorkload:
    """Safe two-client PostgreSQL advisory-lock workload."""

    mode: str
    clients: int
    samples: int
    hold_ms: int
    lock_key: int


@dataclass(frozen=True, kw_only=True)
class BenchmarkWorkload:
    """Validated benchmark controls and required query set."""

    workload_version: int
    warmup_count: int
    sample_count: int
    operations: tuple[BenchmarkQuery, ...]
    contention: ContentionWorkload


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"benchmark {name} must be a positive integer")
    return value


def _parse_operation(raw: object) -> BenchmarkQuery:
    if not isinstance(raw, dict):
        raise ValueError("benchmark operation must be an object")
    name = raw.get("name")
    coverage = raw.get("coverage")
    operation = raw.get("operation")
    params = raw.get("params")
    if not isinstance(name, str) or not isinstance(coverage, str):
        raise ValueError("benchmark operation name and coverage must be text")
    if not isinstance(operation, str) or operation not in _OPERATION_COVERAGE:
        raise ValueError(f"unknown benchmark operation: {operation!r}")
    if _OPERATION_COVERAGE[operation] != coverage:
        raise ValueError("benchmark operation coverage mismatch")
    if not isinstance(params, dict) or not all(
        isinstance(key, str) and type(item) is int and item > 0
        for key, item in params.items()
    ):
        raise ValueError("benchmark operation params must be positive integers")
    if set(params) != _OPERATION_PARAMS[operation]:
        raise ValueError(f"benchmark operation {operation} params mismatch")
    return BenchmarkQuery(
        name=name,
        coverage=coverage,
        operation=operation,
        params=params,
    )


def _parse_operations(value: object) -> tuple[BenchmarkQuery, ...]:
    if not isinstance(value, list):
        raise ValueError("benchmark operations must be a list")
    operations = tuple(_parse_operation(raw) for raw in value)
    coverages = {operation.coverage for operation in operations}
    if coverages != REQUIRED_BENCHMARK_COVERAGE:
        raise ValueError("benchmark coverage mismatch")
    if len({operation.name for operation in operations}) != len(operations):
        raise ValueError("benchmark operation names must be unique")
    return operations


def _parse_contention(value: object) -> ContentionWorkload:
    if not isinstance(value, dict):
        raise ValueError("benchmark contention must be an object")
    mode = value.get("operation")
    clients = value.get("clients")
    if mode != "two_client_write_lock" or clients != _CONTENTION_CLIENTS:
        raise ValueError("benchmark contention requires exact two-client mode")
    return ContentionWorkload(
        mode=mode,
        clients=clients,
        samples=_positive_int(value.get("samples"), "contention samples"),
        hold_ms=_positive_int(value.get("hold_ms"), "contention hold_ms"),
        lock_key=_positive_int(value.get("lock_key"), "contention lock_key"),
    )


def validate_benchmark_workload(document: object) -> BenchmarkWorkload:
    """Parse the exact v1 read-only benchmark workload.

    Args:
        document: JSON-decoded workload candidate.

    Returns:
        Immutable validated workload.

    Raises:
        ValueError: If metadata, coverage, SQL, or contention controls differ.
    """
    if not isinstance(document, dict):
        raise ValueError("benchmark workload must be an object")
    if document.get("workload_version") != 1:
        raise ValueError("unknown benchmark workload version")
    warmups = _positive_int(document.get("warmup_count"), "warmup_count")
    samples = _positive_int(document.get("sample_count"), "sample_count")
    operations = _parse_operations(document.get("operations"))
    contention = _parse_contention(document.get("contention"))
    return BenchmarkWorkload(
        workload_version=1,
        warmup_count=warmups,
        sample_count=samples,
        operations=operations,
        contention=contention,
    )


def _json_bytes(document: object) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def artifact_sha256(document: object) -> str:
    """Return the SHA-256 used for canonical JSON artifact bytes.

    Args:
        document: JSON-serializable artifact.

    Returns:
        Lowercase SHA-256 hex digest.
    """
    return hashlib.sha256(_json_bytes(document)).hexdigest()


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(_percentile(samples, 0.50), 6),
        "p95_ms": round(_percentile(samples, 0.95), 6),
        "max_ms": round(max(samples), 6),
    }
