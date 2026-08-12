"""PostgreSQL baseline benchmark report construction."""

from __future__ import annotations

from dataclasses import dataclass

from jobfeed.adapters.migration._baseline_workload import (
    BenchmarkWorkload,
    _summary,
    artifact_sha256,
)
from jobfeed.adapters.migration._pg_benchmark_runner import StoreBenchmarkResult
from jobfeed.adapters.migration._pg_claim_contention import ClaimContentionResult
from jobfeed.adapters.migration._pg_scratch_mutations import ScratchMutationResult


@dataclass(frozen=True, kw_only=True)
class ReportContext:
    """Immutable inputs for the acyclic store benchmark report."""

    captured_at: str
    git_commit: str
    manifest_sha256: str
    workload_document: object
    workload: BenchmarkWorkload
    store_results: list[StoreBenchmarkResult]
    machine_fingerprint: str
    machine_token_sha256: str
    cpu_identifier_sha256: str
    source_gate: tuple[str, int, int]
    source_post_gate: tuple[str, int, int]
    scratch_gate: tuple[str, int, int]
    scratch_post_gate: tuple[str, int, int]
    contention: ClaimContentionResult
    persisted_claim_ids: list[str]
    scratch_mutations: ScratchMutationResult


def _scratch_mutation_report(
    result: ScratchMutationResult, sample_count: int
) -> dict[str, object]:
    return {
        "mode": "disposable_scratch_real_writes",
        "setup_in_timed_samples": False,
        "sample_count": sample_count,
        "scan": {
            "operation": "save_job_insert_then_quality_upgrade",
            "verified_rows": len(result.scan_canonical_ids),
            **_summary(result.scan_result.samples_ms),
        },
        "evaluate": {
            "operation": "claim_release_result_error",
            "verified_rows": len(result.expected_evaluation_states),
            **_summary(result.evaluate_result.samples_ms),
            "paths": {
                name: {"sample_count": len(samples), **_summary(samples)}
                for name, samples in result.path_samples_ms.items()
            },
        },
    }


def build_benchmark_report(context: ReportContext) -> dict[str, object]:
    """Build a benchmark artifact that points only to prior evidence.

    Args:
        context: Validated measurements, gates, and one-way hash inputs.

    Returns:
        Exact v1 benchmark document.
    """
    workload = context.workload
    contention = context.contention
    source_revision, source_writers, source_running = context.source_gate
    post_revision, post_writers, post_running = context.source_post_gate
    scratch_revision, scratch_writers, scratch_running = context.scratch_gate
    scratch_post_revision, scratch_post_writers, scratch_post_running = (
        context.scratch_post_gate
    )
    query_reports = [
        {
            "name": query.name,
            "coverage": query.coverage,
            "row_count": result.row_count,
            **_summary(result.samples_ms),
        }
        for query, result in zip(
            workload.operations, context.store_results, strict=True
        )
    ]
    successful_claims = sum(
        len(claims) for claims in contention.claimed_by_process.values()
    )
    return {
        "report_version": 1,
        "created_at_utc": context.captured_at,
        "git_commit": context.git_commit,
        "snapshot_manifest_sha256": context.manifest_sha256,
        "workload_sha256": artifact_sha256(context.workload_document),
        "machine_fingerprint": context.machine_fingerprint,
        "machine_token_sha256": context.machine_token_sha256,
        "cpu_identifier_sha256": context.cpu_identifier_sha256,
        "warmup_count": workload.warmup_count,
        "sample_count": workload.sample_count,
        "read_consistency": {
            "mode": "quiescent_pre_post_gate_with_fresh_rehash",
            "canonical_manifest": "initial repeatable-read read-only transaction",
            "store_metrics": "separate connections followed by fresh full rehash",
            "contention": "distinct attested disposable scratch restore only",
            "pre_revision": source_revision,
            "pre_active_writers": source_writers,
            "pre_running_runs": source_running,
            "post_revision": post_revision,
            "post_active_writers": post_writers,
            "post_running_runs": post_running,
        },
        "queries": query_reports,
        "contention": {
            "mode": workload.contention.mode,
            "processes": workload.contention.processes,
            "worker_pids": sorted(contention.claimed_by_process),
            "successful_claims_by_process": {
                str(pid): len(claims)
                for pid, claims in contention.claimed_by_process.items()
            },
            "coroutines_per_process": workload.contention.coroutines_per_process,
            "rounds_per_coroutine": workload.contention.rounds_per_coroutine,
            "attempted_short_writes": (
                workload.contention.processes
                * workload.contention.coroutines_per_process
                * workload.contention.rounds_per_coroutine
            ),
            "successful_claims": successful_claims,
            "database_claim_count": len(context.persisted_claim_ids),
            "database_claim_ids_sha256": artifact_sha256(
                sorted(context.persisted_claim_ids, key=int)
            ),
            "empty_claims": contention.empty_claims,
            "duplicate_claims": 0,
            "data_loss": 0,
            "retry_exhausted_busy": 0,
            "scratch_initial_manifest_sha256": context.manifest_sha256,
            "scratch_pre_revision": scratch_revision,
            "scratch_pre_active_writers": scratch_writers,
            "scratch_pre_running_runs": scratch_running,
            "scratch_post_revision": scratch_post_revision,
            "scratch_post_active_writers": scratch_post_writers,
            "scratch_post_running_runs": scratch_post_running,
            **_summary(contention.samples_ms),
        },
        "scratch_mutations": _scratch_mutation_report(
            context.scratch_mutations, workload.sample_count
        ),
    }
