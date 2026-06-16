"""ML-gate scoring and persistence helpers extracted from the funnel.

Separates the gate logic (predict + persist + partition) from the load +
hard-filter + dedupe loop so ``_evaluate_funnel.py`` stays under the 300-line
limit after instrumentation is added.
"""

from __future__ import annotations

import asyncio

from jobfeed.domain.models import JobPosting, MLGateResult, PipelineRun
from jobfeed.ports.ml_gate import GateInput, MLGate
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig


async def gate_representatives(
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    representatives: list[GateCandidate],
    dry_run: bool,
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

    Returns:
        Surviving job postings (all reps' jobs when the gate is off).
    """
    gate = deps.ml_gate
    if not config.ml_gate_enabled or gate is None:
        return [c.job for c in representatives]
    already_pass = [c.job for c in representatives if c.ml_gate_result == "pass"]
    to_gate = [c.job for c in representatives if c.ml_gate_result is None]
    newly_passed = await gate_unrated(deps, gate, run, to_gate, dry_run)
    return already_pass + newly_passed


async def gate_unrated(
    deps: EvaluateDependencies,
    gate: MLGate,
    run: PipelineRun,
    to_gate: list[JobPosting],
    dry_run: bool,
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

    Returns:
        Job postings that passed the gate.
    """
    if not to_gate:
        return []
    inputs = [
        GateInput(job_id=_job_id(job), title=job.title, jd_text=job.jd_text or "")
        for job in to_gate
    ]
    results = await asyncio.to_thread(_predict_sync, gate, inputs)
    await _persist_gate_results(deps, to_gate, results, dry_run)
    run.jobs_ml_gated += sum(1 for result in results if result.result != "pass")
    return [
        job
        for job, result in zip(to_gate, results, strict=True)
        if result.result == "pass"
    ]


def _predict_sync(gate: MLGate, inputs: list[GateInput]) -> list[MLGateResult]:
    """Run the gate coroutine in a fresh event loop inside a worker thread.

    ``predict_batch`` is async in the port protocol but performs synchronous
    CPU-bound work (XGBoost / numpy). Running it in ``asyncio.to_thread`` keeps
    the main loop responsive; the thread-local ``asyncio.run`` provides the
    event loop the coroutine needs without touching the caller's loop.
    """
    return asyncio.run(gate.predict_batch(inputs))


async def _persist_gate_results(
    deps: EvaluateDependencies,
    representatives: list[JobPosting],
    results: list[MLGateResult],
    dry_run: bool,
) -> None:
    """Persist each gate result concurrently, 1:1 with reps (no-op in dry-run)."""
    if dry_run:
        return
    pairs = list(zip(representatives, results, strict=True))

    async def _save(job: JobPosting, result: MLGateResult) -> None:
        await deps.store.save_ml_gate_result(_job_id(job), result)

    await asyncio.gather(*(_save(job, result) for job, result in pairs))


def _job_id(job: JobPosting) -> str:
    """Return the store-assigned id, raising if a funnel job lacks one."""
    if job.id is None:
        raise ValueError("funnel candidates must have a store id")
    return job.id


__all__ = ["gate_representatives", "gate_unrated"]
