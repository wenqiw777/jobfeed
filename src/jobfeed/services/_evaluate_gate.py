"""ML-gate scoring and persistence helpers extracted from the funnel.

Separates the gate logic (predict + persist + partition) from the load +
hard-filter + dedupe loop so ``_evaluate_funnel.py`` stays under the 300-line
limit after instrumentation is added.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from jobfeed.domain.models import JobPosting, MLGateResult, PipelineRun
from jobfeed.ports.ml_gate import GateInput, MLGate
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig


async def gate_representatives(  # noqa: PLR0913 - live progress is an optional observer
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    representatives: list[GateCandidate],
    dry_run: bool,
    *,
    on_progress: Callable[[], None] | None = None,
) -> list[JobPosting]:
    """Gate only NULL-gate reps and return the surviving job postings.

    Already-'pass' representatives (e.g. a crash between gate-write and Stage-A
    claim) become survivors directly WITHOUT re-gating or re-persisting, per plan
    Decision 8 -- re-gating them risks a swapped model / changed
    ``threshold_override`` flipping a persisted 'pass' to 'fail' and silently
    dropping the row from scoring. 'fail' reps are already excluded by the load
    predicate when ``exclude_gate_failed`` (i.e. whenever the gate runs).

    Args:
        deps: Evaluate dependencies holding the optional ML gate + store.
        config: Runtime config carrying the ml_gate flag.
        run: Pipeline run whose ``jobs_ml_gated`` counter is incremented.
        representatives: Deduped survivors (job + gate state) to consider.
        dry_run: When True, skip persistence.
        on_progress: Optional bounded callback after persisted gate results.

    Returns:
        Surviving job postings (all reps' jobs when the gate is off).
    """
    already_pass = [c.job for c in representatives if c.ml_gate_result == "pass"]
    to_gate = [c.job for c in representatives if c.ml_gate_result is None]
    run.ml_gate_total = len(representatives)
    run.ml_gate_processed = len(already_pass)
    if on_progress is not None:
        on_progress()
    gate = deps.ml_gate
    if not config.ml_gate_enabled or gate is None:
        run.ml_gate_processed = len(representatives)
        if on_progress is not None:
            on_progress()
        return [c.job for c in representatives]
    update_interval = max(1, (len(to_gate) + 49) // 50)

    def _result_persisted() -> None:
        run.ml_gate_processed += 1
        completed = run.ml_gate_processed - len(already_pass)
        if on_progress is not None and (
            completed % update_interval == 0
            or run.ml_gate_processed == run.ml_gate_total
        ):
            on_progress()

    newly_passed = await gate_unrated(
        deps,
        gate,
        run,
        to_gate,
        dry_run,
        max_concurrent=config.llm.max_concurrent,
        on_persisted=_result_persisted,
    )
    if dry_run and to_gate:
        run.ml_gate_processed = len(representatives)
        if on_progress is not None:
            on_progress()
    survivors = already_pass + newly_passed
    # The survivor count is the funnel's "after gate" stage; the scored
    # counters can't stand in for it (Stage A limit/budget cap them).
    run.jobs_gate_passed += len(survivors)
    return survivors


async def gate_unrated(  # noqa: PLR0913 - persistence needs the shared concurrency cap
    deps: EvaluateDependencies,
    gate: MLGate,
    run: PipelineRun,
    to_gate: list[JobPosting],
    dry_run: bool,
    *,
    max_concurrent: int = 4,
    on_persisted: Callable[[], None] | None = None,
) -> list[JobPosting]:
    """Score NULL-gate reps, persist results, and return the 'pass' subset.

    Increments ``run.jobs_ml_gated`` by the non-pass count; persists every fresh
    result (no-op in dry-run).

    Args:
        deps: Evaluate dependencies holding the store.
        gate: ML gate adapter.
        run: Pipeline run whose counters are mutated.
        to_gate: Job postings to score (NULL-gate only).
        dry_run: When True, skip persistence.
        max_concurrent: Maximum simultaneous result writes.
        on_persisted: Optional callback after each successful result write.

    Returns:
        Job postings that passed the gate.
    """
    if not to_gate:
        return []
    inputs = [
        GateInput(job_id=_job_id(job), title=job.title, jd_text=job.jd_text or "")
        for job in to_gate
    ]
    # The port is honored as-is: keeping the main loop responsive during
    # CPU-bound inference is the adapter's job (XGBoostGate offloads to a
    # worker thread internally), so an async gate implementation stays legal.
    results = await gate.predict_batch(inputs)
    await _persist_gate_results(
        deps,
        to_gate,
        results,
        dry_run,
        max_concurrent=max_concurrent,
        on_persisted=on_persisted,
    )
    run.jobs_ml_gated += sum(1 for result in results if result.result != "pass")
    return [
        job
        for job, result in zip(to_gate, results, strict=True)
        if result.result == "pass"
    ]


async def _persist_gate_results(  # noqa: PLR0913 - bounded persistence needs its observer
    deps: EvaluateDependencies,
    representatives: list[JobPosting],
    results: list[MLGateResult],
    dry_run: bool,
    *,
    max_concurrent: int,
    on_persisted: Callable[[], None] | None,
) -> None:
    """Persist gate results with bounded fan-out, 1:1 with representatives."""
    if dry_run:
        return
    pairs = list(zip(representatives, results, strict=True))

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def _save(job: JobPosting, result: MLGateResult) -> None:
        async with semaphore:
            await deps.store.save_ml_gate_result(_job_id(job), result)
            if on_persisted is not None:
                on_persisted()

    await asyncio.gather(*(_save(job, result) for job, result in pairs))


def _job_id(job: JobPosting) -> str:
    """Return the store-assigned id, raising if a funnel job lacks one."""
    if job.id is None:
        raise ValueError("funnel candidates must have a store id")
    return job.id


__all__ = ["gate_representatives", "gate_unrated"]
