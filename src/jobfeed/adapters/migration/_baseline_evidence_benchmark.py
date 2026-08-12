"""Exact semantic validation for one PostgreSQL benchmark report."""

from __future__ import annotations

from jobfeed.adapters.migration import _baseline_evidence_shape as shape
from jobfeed.adapters.migration._baseline_workload import REQUIRED_BENCHMARK_COVERAGE

_MIN_SAMPLES = 30
_EXPECTED_PROCESSES = 2
_EXPECTED_COROUTINES = 8
_MINIMUM_SUCCESSFUL_CLAIMS = 100


def _validate_queries(document: dict[str, object]) -> None:
    """Validate query reports.

    Time complexity is O(queries * metrics-per-query).
    """
    queries = document["queries"]
    if not isinstance(queries, list):
        raise ValueError("benchmark queries must be a list")
    names: set[str] = set()
    coverages: set[str] = set()
    for position, query_value in enumerate(queries):
        name = f"benchmark.queries[{position}]"
        query = shape.mapping(query_value, name)
        shape.exact_keys(query, shape.QUERY_KEYS, name)
        names.add(shape.text(query["name"], f"{name}.name"))
        coverages.add(shape.text(query["coverage"], f"{name}.coverage"))
        shape.integer(query["row_count"], f"{name}.row_count", minimum=1)
        for metric in ("p50_ms", "p95_ms", "max_ms"):
            shape.number(query[metric], f"{name}.{metric}")
    if len(names) != len(queries):
        raise ValueError("benchmark query names must be unique")
    if coverages != REQUIRED_BENCHMARK_COVERAGE or len(queries) != len(coverages):
        raise ValueError("benchmark query coverage mismatch")


def _validate_read_consistency(document: dict[str, object]) -> None:
    value = shape.mapping(document["read_consistency"], "benchmark.read_consistency")
    shape.exact_keys(value, shape.READ_CONSISTENCY_KEYS, "benchmark.read_consistency")
    expected = {
        "mode": "quiescent_pre_post_gate_with_fresh_rehash",
        "canonical_manifest": "initial repeatable-read read-only transaction",
        "store_metrics": "separate connections followed by fresh full rehash",
        "contention": "distinct attested disposable scratch restore only",
        "pre_revision": "0008",
        "pre_active_writers": 0,
        "pre_running_runs": 0,
        "post_revision": "0008",
        "post_active_writers": 0,
        "post_running_runs": 0,
    }
    if value != expected:
        raise ValueError("benchmark read consistency gate mismatch")


def _validate_contention(document: dict[str, object]) -> None:
    value = shape.mapping(document["contention"], "benchmark.contention")
    shape.exact_keys(value, shape.CONTENTION_KEYS, "benchmark.contention")
    if (
        value["mode"] != "claim_pending_stage_a"
        or value["processes"] != _EXPECTED_PROCESSES
        or value["coroutines_per_process"] != _EXPECTED_COROUTINES
    ):
        raise ValueError("benchmark contention topology mismatch")
    rounds = shape.integer(
        value["rounds_per_coroutine"], "contention rounds", minimum=100
    )
    attempted = _EXPECTED_PROCESSES * _EXPECTED_COROUTINES * rounds
    if value["attempted_short_writes"] != attempted:
        raise ValueError("benchmark contention attempted-write count mismatch")
    pids = value["worker_pids"]
    by_process = shape.mapping(
        value["successful_claims_by_process"], "successful claims by process"
    )
    process_evidence_invalid = (
        not isinstance(pids, list)
        or len(pids) != _EXPECTED_PROCESSES
        or len(set(pids)) != _EXPECTED_PROCESSES
        or set(by_process) != {str(pid) for pid in pids}
        or any(type(count) is not int or count <= 0 for count in by_process.values())
    )
    if process_evidence_invalid:
        raise ValueError("benchmark contention process evidence mismatch")
    successful = sum(
        shape.integer(count, "successful claim count", minimum=1)
        for count in by_process.values()
    )
    if (
        successful < _MINIMUM_SUCCESSFUL_CLAIMS
        or value["successful_claims"] != successful
    ):
        raise ValueError("benchmark contention successful claim count mismatch")
    if value["database_claim_count"] != successful:
        raise ValueError("benchmark contention database claim count mismatch")
    for key in ("database_claim_ids_sha256", "scratch_initial_manifest_sha256"):
        shape.sha(value[key], f"benchmark.contention.{key}")
    _validate_contention_gates(value)
    shape.integer(value["empty_claims"], "benchmark.contention.empty_claims")
    for metric in ("p50_ms", "p95_ms", "max_ms"):
        shape.number(value[metric], f"benchmark.contention.{metric}")


def _validate_contention_gates(value: dict[str, object]) -> None:
    for key in (
        "duplicate_claims",
        "data_loss",
        "retry_exhausted_busy",
        "scratch_pre_active_writers",
        "scratch_pre_running_runs",
        "scratch_post_active_writers",
        "scratch_post_running_runs",
    ):
        if value[key] != 0:
            raise ValueError(f"benchmark contention {key} must be zero")
    if (
        value["scratch_pre_revision"] != "0008"
        or value["scratch_post_revision"] != "0008"
    ):
        raise ValueError("benchmark contention scratch revision mismatch")


def validate_benchmark_document(document: dict[str, object]) -> None:
    """Validate the complete nested v1 benchmark report shape and semantics.

    Args:
        document: Decoded benchmark report.

    Raises:
        ValueError: If any shape, type, or correctness gate differs.
    """
    shape.exact_keys(document, shape.BENCHMARK_KEYS, "benchmark")
    if document["report_version"] != 1:
        raise ValueError("unknown benchmark version")
    shape.text(document["created_at_utc"], "benchmark.created_at_utc")
    shape.text(document["git_commit"], "benchmark.git_commit")
    shape.sha(document["snapshot_manifest_sha256"], "benchmark manifest SHA")
    shape.sha(document["workload_sha256"], "benchmark workload SHA")
    for key in ("machine_fingerprint", "machine_token_sha256", "cpu_identifier_sha256"):
        shape.sha(document[key], f"benchmark.{key}")
    shape.integer(document["warmup_count"], "benchmark.warmup_count", minimum=1)
    shape.integer(
        document["sample_count"], "benchmark.sample_count", minimum=_MIN_SAMPLES
    )
    _validate_queries(document)
    _validate_read_consistency(document)
    _validate_contention(document)
    open_workloads = document["open_workloads"]
    if (
        not isinstance(open_workloads, list)
        or set(open_workloads) != shape.OPEN_WORKLOADS
    ):
        raise ValueError("benchmark open workloads mismatch")
