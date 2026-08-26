"""ML-gate scoring and persistence helpers extracted from the funnel.

Separates the gate logic (predict + persist + partition) from the load +
hard-filter + dedupe loop so ``_evaluate_funnel.py`` stays under the 300-line
limit after instrumentation is added.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Literal

from jobfeed.domain.models import JobPosting, MLGateResult, PipelineRun
from jobfeed.ports.ml_gate import GateInput, MLGate
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig

GateMode = Literal["off", "shadow", "filter"]
_EXPLORATION_BUCKETS = 10
_EXPLICIT_NON_SDE_REASON = "not software engineering role"


async def gate_representatives(  # noqa: PLR0913 - live progress is an optional observer
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    representatives: list[GateCandidate],
    dry_run: bool,
    *,
    mode: GateMode | None = None,
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
    active_mode = mode or await resolve_gate_mode(deps, config, dry_run=dry_run)
    if gate is not None and active_mode == "shadow":
        await _score_in_shadow(
            deps,
            gate,
            run,
            representatives,
            max_concurrent=config.llm.max_concurrent,
            on_persisted=on_progress,
        )
        return [candidate.job for candidate in representatives]
    if active_mode != "filter" or gate is None:
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


async def resolve_gate_mode(
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    *,
    dry_run: bool,
) -> GateMode:
    """Resolve off, safe-shadow, or filtering before the candidate query.

    Args:
        deps: Evaluation dependencies including optional personal-ML state.
        config: Effective evaluation and gate configuration.
        dry_run: Whether evaluation will avoid persistence and model calls.

    Returns:
        Gate mode to apply to this evaluation run.
    """
    if deps.ml_gate is None:
        return "off"
    if deps.personal_ml is None:
        return "filter" if config.ml_gate_enabled else "off"
    status = await deps.personal_ml.status(
        quick_pass_threshold=config.stage_a_threshold,
        enabled=config.ml_gate_enabled,
    )
    if status.state == "paused":
        return "shadow"
    if config.ml_gate_enabled:
        return "filter"
    if not dry_run and status.state in {"ranking", "shadow", "ready"}:
        return "shadow"
    return "off"


async def _score_in_shadow(  # noqa: PLR0913 - persistence uses shared gate controls
    deps: EvaluateDependencies,
    gate: MLGate,
    run: PipelineRun,
    representatives: list[GateCandidate],
    *,
    max_concurrent: int,
    on_persisted: Callable[[], None] | None,
) -> None:
    """Persist missing predictions while returning every candidate to Quick."""
    to_score = [
        candidate.job
        for candidate in representatives
        if candidate.ml_gate_result is None
    ]
    run.ml_gate_total = len(representatives)
    run.ml_gate_processed = len(representatives) - len(to_score)
    if not to_score:
        return
    results = await gate.predict_batch(
        [
            GateInput(job_id=_job_id(job), title=job.title, jd_text=job.jd_text or "")
            for job in to_score
        ]
    )
    results = [_as_local_filter_result(result) for result in results]

    def _persisted() -> None:
        run.ml_gate_processed += 1
        if on_persisted is not None:
            on_persisted()

    await _persist_gate_results(
        deps,
        to_score,
        results,
        False,
        max_concurrent=max_concurrent,
        on_persisted=_persisted,
    )


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
    """Score NULL-gate reps and return every role except explicit non-SDE jobs.

    Model scores, years of experience, and clearance are retained on the stored
    result but cannot block Quick evaluation. Only a deterministic
    ``not software engineering role`` failure remains a local-filter exclusion.
    Ten percent of those clear non-SDE failures are explored for recall labels.

    Args:
        deps: Evaluate dependencies holding the store.
        gate: ML gate adapter.
        run: Pipeline run whose counters are mutated.
        to_gate: Job postings to score (NULL-gate only).
        dry_run: When True, skip persistence.
        max_concurrent: Maximum simultaneous result writes.
        on_persisted: Optional callback after each successful result write.

    Returns:
        Job postings that can proceed to Quick evaluation.
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
    raw_results = await gate.predict_batch(inputs)
    results = [_as_local_filter_result(result) for result in raw_results]
    await _persist_gate_results(
        deps,
        to_gate,
        results,
        dry_run,
        max_concurrent=max_concurrent,
        on_persisted=on_persisted,
    )
    explored = {
        _job_id(job)
        for job, result in zip(to_gate, results, strict=True)
        if _is_explicit_non_sde_failure(result) and _is_exploration(job)
    }
    run.jobs_ml_gated += sum(
        _is_explicit_non_sde_failure(result) and _job_id(job) not in explored
        for job, result in zip(to_gate, results, strict=True)
    )
    return [
        job
        for job, result in zip(to_gate, results, strict=True)
        if result.result == "pass" or _job_id(job) in explored
    ]


def _is_exploration(job: JobPosting) -> bool:
    """Select a stable 10% of explicit non-SDE failures for Quick labels."""
    job_id = _job_id(job)
    return job_id.isdigit() and int(job_id) % _EXPLORATION_BUCKETS == 0


def _as_local_filter_result(result: MLGateResult) -> MLGateResult:
    """Keep score/features but turn non-SDE-irrelevant failures into passes.

    The adapter continues to report its raw model verdict for observability.
    At the evaluation boundary, however, a low model score is a nonblocking
    signal. This prevents a model threshold, clearance-like legacy verdict, or
    other non-SDE reason from creating a persisted exclusion on future runs.
    """
    if result.result == "pass" or _is_explicit_non_sde_failure(result):
        return result
    return replace(result, result="pass", fail_reason=None)


def _is_explicit_non_sde_failure(result: MLGateResult) -> bool:
    """Return whether a gate result is the sole local-filter exclusion type."""
    return result.result == "fail" and result.fail_reason == _EXPLICIT_NON_SDE_REASON


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


__all__ = ["gate_representatives", "gate_unrated", "resolve_gate_mode"]
