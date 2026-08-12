"""Merge separately measured read and scratch mutation benchmark results."""

from __future__ import annotations

from jobfeed.adapters.migration._baseline_workload import BenchmarkQuery
from jobfeed.adapters.migration._pg_benchmark_runner import StoreBenchmarkResult
from jobfeed.adapters.migration._pg_scratch_mutations import ScratchMutationResult


def merge_benchmark_results(
    operations: tuple[BenchmarkQuery, ...],
    read_results: list[StoreBenchmarkResult],
    mutations: ScratchMutationResult,
) -> list[StoreBenchmarkResult]:
    """Restore workload order across read-only and mutation measurements.

    Args:
        operations: Frozen workload operation order.
        read_results: Read-only source measurements.
        mutations: Scratch-only write measurements.

    Returns:
        Measurements in original workload order.

    Raises:
        ValueError: If a read result remains unused.
    """
    read_iterator = iter(read_results)
    merged = []
    for operation in operations:
        if operation.coverage == "overhead.scan":
            merged.append(mutations.scan_result)
        elif operation.coverage == "overhead.evaluate":
            merged.append(mutations.evaluate_result)
        else:
            merged.append(next(read_iterator))
    if next(read_iterator, None) is not None:
        raise ValueError("unused read benchmark result")
    return merged
