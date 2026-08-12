"""Exact semantic validation for one PostgreSQL benchmark report."""

from __future__ import annotations

from jobfeed.adapters.migration import _baseline_evidence_shape as shape
from jobfeed.adapters.migration._baseline_workload import (
    ALLOW_EMPTY_BENCHMARK_COVERAGE,
    REQUIRED_BENCHMARK_COVERAGE,
)

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
        coverage = shape.text(query["coverage"], f"{name}.coverage")
        coverages.add(coverage)
        minimum = 0 if coverage in ALLOW_EMPTY_BENCHMARK_COVERAGE else 1
        shape.integer(query["row_count"], f"{name}.row_count", minimum=minimum)
        shape.timing_summary(query, name)
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
    empty = shape.integer(value["empty_claims"], "benchmark.contention.empty_claims")
    if successful + empty != attempted:
        raise ValueError("benchmark contention successful+empty must equal attempted")
    for key in ("database_claim_ids_sha256", "scratch_initial_manifest_sha256"):
        shape.sha(value[key], f"benchmark.contention.{key}")
    _validate_contention_gates(value)
    shape.timing_summary(value, "benchmark.contention")


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


def _validate_mutation_metrics(
    value: dict[str, object], expected: set[str], name: str
) -> None:
    shape.exact_keys(value, expected, name)
    shape.integer(value["verified_rows"], f"{name}.verified_rows", minimum=1)
    shape.timing_summary(value, name)


def _validate_scratch_mutations(document: dict[str, object]) -> None:
    value = shape.mapping(document["scratch_mutations"], "benchmark.scratch_mutations")
    shape.exact_keys(value, shape.SCRATCH_MUTATION_KEYS, "benchmark.scratch_mutations")
    if (
        value["mode"] != "disposable_scratch_real_writes"
        or value["setup_in_timed_samples"] is not False
    ):
        raise ValueError("benchmark scratch mutation mode mismatch")
    sample_count = shape.integer(
        value["sample_count"], "benchmark scratch sample count", minimum=_MIN_SAMPLES
    )
    scan = shape.mapping(value["scan"], "benchmark.scratch_mutations.scan")
    _validate_mutation_metrics(
        scan, shape.SCAN_MUTATION_KEYS, "benchmark.scratch_mutations.scan"
    )
    if scan["operation"] != "save_job_insert_then_quality_upgrade":
        raise ValueError("benchmark scan mutation operation mismatch")
    evaluate = shape.mapping(value["evaluate"], "benchmark.scratch_mutations.evaluate")
    _validate_mutation_metrics(
        evaluate,
        shape.EVALUATE_MUTATION_KEYS,
        "benchmark.scratch_mutations.evaluate",
    )
    if evaluate["operation"] != "claim_release_result_error":
        raise ValueError("benchmark evaluate mutation operation mismatch")
    paths = shape.mapping(evaluate["paths"], "benchmark scratch mutation paths")
    shape.exact_keys(paths, shape.EVALUATE_PATHS, "benchmark scratch mutation paths")
    for path, raw in paths.items():
        metrics = shape.mapping(raw, f"benchmark scratch path {path}")
        shape.exact_keys(metrics, shape.PATH_MUTATION_KEYS, f"scratch path {path}")
        if metrics["sample_count"] != sample_count:
            raise ValueError("benchmark scratch path sample count mismatch")
        shape.timing_summary(metrics, f"benchmark scratch path {path}")


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
    _validate_scratch_mutations(document)
