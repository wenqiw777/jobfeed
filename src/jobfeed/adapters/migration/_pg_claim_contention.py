"""Real PostgreSQL claim contention workload for migration baselines."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from dataclasses import dataclass
from queue import Empty
from typing import Any

from jobfeed.adapters.migration._baseline_workload import ContentionWorkload
from jobfeed.adapters.store.postgres import PostgresStore

_WORKER_TIMEOUT_SECONDS = 300
_EXPECTED_PROCESSES = 2
_MINIMUM_SUCCESSFUL_WRITES = 100


@dataclass(frozen=True, kw_only=True)
class ClaimContentionResult:
    """Observed process, claim identity, timing, and error evidence."""

    claimed_by_process: dict[int, list[str]]
    samples_ms: list[float]
    empty_claims: int
    errors: list[str]


@dataclass(frozen=True, kw_only=True)
class _WorkerConfig:
    dsn: str
    coroutines: int
    rounds: int
    claim_limit: int


def validate_claim_contention_outcome(
    *,
    claimed_by_process: dict[int, list[str]],
    errors: list[str],
    persisted_claim_ids: list[str],
) -> None:
    """Fail closed on missing processes, duplicate claims, or worker errors.

    Args:
        claimed_by_process: Claimed identities grouped by reporting OS process.
        errors: Worker exception summaries.
        persisted_claim_ids: Final claimed IDs selected by the DB cutoff query.

    Raises:
        ValueError: If the workload did not prove its correctness gates.
    """
    if len(claimed_by_process) != _EXPECTED_PROCESSES:
        raise ValueError("claim contention did not use two distinct OS processes")
    if errors:
        raise ValueError(f"claim contention worker error: {errors[0]}")
    if any(not claims for claims in claimed_by_process.values()):
        raise ValueError("each contention process must claim at least one job")
    claimed_ids = [
        job_id for claims in claimed_by_process.values() for job_id in claims
    ]
    if len(set(claimed_ids)) != len(claimed_ids):
        raise ValueError("claim contention produced a duplicate claim")
    if len(claimed_ids) < _MINIMUM_SUCCESSFUL_WRITES:
        raise ValueError("claim contention produced fewer than 100 short writes")
    if sorted(persisted_claim_ids, key=int) != sorted(claimed_ids, key=int):
        raise ValueError("claim contention persisted claim ID set mismatch")


async def _run_worker_async(
    config: _WorkerConfig,
    ready: Any,
    start: Any,
) -> dict[str, object]:
    store = PostgresStore(
        config.dsn, min_size=config.coroutines, max_size=config.coroutines
    )
    await store.connect()
    ready.put(os.getpid())
    if not start.wait(timeout=30):
        raise RuntimeError("claim contention start event timed out")

    async def claim_loop() -> tuple[list[str], list[float], int, list[str]]:
        claimed_ids: list[str] = []
        timings: list[float] = []
        empty = 0
        errors: list[str] = []
        for _ in range(config.rounds):
            started = time.perf_counter_ns()
            try:
                jobs = await store.claim_pending_stage_a(limit=config.claim_limit)
            except Exception as exc:  # workload evidence must preserve adapter errors
                errors.append(f"{type(exc).__name__}: {exc}")
                break
            timings.append((time.perf_counter_ns() - started) / 1_000_000)
            if jobs:
                claimed_ids.extend(str(job.id) for job in jobs)
            else:
                empty += 1
        return claimed_ids, timings, empty, errors

    try:
        results = await asyncio.gather(
            *(claim_loop() for _ in range(config.coroutines))
        )
    finally:
        await store.close()
    return {
        "pid": os.getpid(),
        "claimed_ids": [job_id for result in results for job_id in result[0]],
        "samples_ms": [sample for result in results for sample in result[1]],
        "empty_claims": sum(result[2] for result in results),
        "errors": [error for result in results for error in result[3]],
    }


def _claim_worker(
    config: _WorkerConfig,
    ready: Any,
    start: Any,
    output: Any,
) -> None:
    try:
        output.put(asyncio.run(_run_worker_async(config, ready, start)))
    except Exception as exc:  # parent converts bootstrap failures into gate evidence
        output.put(
            {
                "pid": os.getpid(),
                "claimed_ids": [],
                "samples_ms": [],
                "empty_claims": 0,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        )


def run_pg_claim_contention(
    dsn: str, workload: ContentionWorkload
) -> ClaimContentionResult:
    """Run the frozen claim workload in two spawned OS processes.

    Time complexity is O(P * C * R), for processes, coroutines, and rounds.

    Args:
        dsn: PostgreSQL rehearsal DSN.
        workload: Validated process/coroutine/round controls.

    Returns:
        Complete contention evidence after correctness validation.

    Raises:
        ValueError: If workers fail, time out, or duplicate a claim.
    """
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    ready = context.Queue()
    start = context.Event()
    processes = [
        context.Process(
            target=_claim_worker,
            args=(
                _WorkerConfig(
                    dsn=dsn,
                    coroutines=workload.coroutines_per_process,
                    rounds=workload.rounds_per_coroutine,
                    claim_limit=workload.claim_limit,
                ),
                ready,
                start,
                output,
            ),
        )
        for _ in range(workload.processes)
    ]
    for process in processes:
        process.start()
    documents = []
    try:
        ready_pids = {
            int(ready.get(timeout=_WORKER_TIMEOUT_SECONDS)) for _ in processes
        }
        if len(ready_pids) != workload.processes:
            raise ValueError("claim contention workers were not distinctly ready")
        start.set()
        for _ in processes:
            documents.append(output.get(timeout=_WORKER_TIMEOUT_SECONDS))
    except Empty as exc:
        raise ValueError("claim contention worker timed out") from exc
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        output.close()
        ready.close()
    claimed_by_process = {
        int(document["pid"]): [str(job_id) for job_id in document["claimed_ids"]]
        for document in documents
    }
    samples_ms = [
        float(sample) for document in documents for sample in document["samples_ms"]
    ]
    errors = [str(error) for document in documents for error in document["errors"]]
    return ClaimContentionResult(
        claimed_by_process=claimed_by_process,
        samples_ms=samples_ms,
        empty_claims=sum(int(document["empty_claims"]) for document in documents),
        errors=errors,
    )
