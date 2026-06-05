"""Non-claiming evaluation funnel: load -> hard-filter -> dedupe -> gate.

The funnel turns eligible Stage A candidates into a survivor job-id list without
claiming anything. Filter + dedupe are unconditional; gating is conditional on
``config.ml_gate_enabled`` AND a wired ``deps.ml_gate``. The caller
(``EvaluateService``) then claims exactly the survivor ids via
``claim_stage_a_by_ids``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from jobfeed.domain.dedupe import pick_representatives
from jobfeed.domain.filtering import HardFilters, apply_hard_filters
from jobfeed.domain.models import (
    DryRunPreviewItem,
    JobPosting,
    MLGateResult,
    PipelineRun,
)
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.ml_gate import GateInput, MLGate
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services._evaluate_claims import load_gate_candidates_for_run
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig

# Generous safety bound on candidate pages scanned per funnel run, so a long
# freshness window that is almost entirely hard-filter-blocked can't spin
# forever; hitting it warns (the scan was truncated, not a silent cap).
_MAX_CANDIDATE_PAGES = 20


async def run_funnel(  # noqa: PLR0913 - distinct funnel inputs; signature fixed by plan
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    corpus: str,
    max_days: int | None,
    *,
    logger: JobfeedLogger,
    dry_run: bool,
    limit: int | None = None,
) -> list[str]:
    """Run the candidate funnel and return survivor Stage A job-ids.

    Flow: load candidates page-by-page (no claim) -> hard filter (count drops),
    overfetching PAST hard-filtered drops until enough survivors or the eligible
    set is exhausted -> dedupe to representatives -> optional ML gate (gate only
    NULL-gate reps; persist + count non-pass; already-'pass' reps survive without
    re-gating). Survivors are the hard-filter ∩ representative ∩ gate-pass set
    (or, gate off, hard-filter ∩ representative). In dry-run nothing is persisted;
    survivors are sliced to the Stage A ``limit`` (matching what a real claim
    would take) and a survivor preview is recorded on ``run.dry_run_preview``.

    Args:
        deps: Evaluate dependencies (store, optional ml_gate / hard_filters).
        config: Runtime config (ml_gate flag + gate-candidate budget / page size).
        run: Pipeline run whose counters are mutated in place.
        corpus: Corpus filter value.
        max_days: Freshness filter on discovered_at.
        logger: Run logger; warns if the candidate scan is page-bound truncated.
        dry_run: When True, persist nothing and record a (limit-sliced) preview.
        limit: Stage A claim limit; slices the dry-run preview to match a real
            run. ``None`` leaves the survivor set unsliced.

    Returns:
        Survivor job-ids to hand to Stage A (empty in dry-run).
    """
    filtered = await _load_filtered_survivors(
        deps, config, run, corpus, max_days, logger
    )
    representatives = _representatives(filtered)
    survivors = await _gate_representatives(deps, config, run, representatives, dry_run)
    if dry_run:
        # Append (not assign) so a later Stage-B preview pass keeps this pass's
        # items; matches build_dry_run_preview's Stage-B ``.extend`` and is
        # identical today (the list starts empty and Stage A runs first).
        run.dry_run_preview.extend(_preview(_limit_survivors(survivors, limit)))
        return []
    return [_job_id(job) for job in survivors]


async def _load_filtered_survivors(  # noqa: PLR0913 - paginated load inputs
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    corpus: str,
    max_days: int | None,
    logger: JobfeedLogger,
) -> list[GateCandidate]:
    """Page candidates, hard-filtering each page, until enough survivors.

    Decouples the LOAD bound from the GATE budget: ``ml_gate_max_candidates`` is
    both the page size and the survivor target, but the load keeps fetching the
    next keyset page (``(discovered_at, id)`` "after" cursor, matching the query's
    ``discovered_at DESC, id DESC`` order) PAST hard-filtered drops so an eligible
    older row behind a blocked newest prefix is not starved. Every page reapplies
    the same store-side predicates (corpus / freshness / stale-takeover /
    twin-suppression / exclude-gate-failed) plus the in-memory hard filter (counted
    on ``run.jobs_filtered``). Stops when survivors reach the page size, a short
    page proves the eligible set is exhausted, or the ``_MAX_CANDIDATE_PAGES``
    safety bound is hit — the last warns so the truncation is never a silent cap.

    Returns:
        Hard-filter survivors capped to ``ml_gate_max_candidates`` (newest-first).
    """
    page_size = config.ml_gate_max_candidates
    survivors: list[GateCandidate] = []
    after: tuple[datetime, int] | None = None
    for _page in range(_MAX_CANDIDATE_PAGES):
        page = await load_gate_candidates_for_run(
            deps.store,
            corpus,
            page_size,
            max_days,
            exclude_gate_failed=config.ml_gate_enabled,
            after=after,
        )
        survivors.extend(_apply_hard_filters(page, deps.hard_filters, run))
        if len(survivors) >= page_size or len(page) < page_size:
            # Cap to the gate budget: a full final page can overshoot to ~2x.
            return survivors[:page_size]  # target met, or set exhausted
        last = page[-1].job
        after = (last.discovered_at, int(_job_id(last)))
    logger.warning("funnel_candidate_scan_truncated", max_pages=_MAX_CANDIDATE_PAGES)
    return survivors[:page_size]


def _apply_hard_filters(
    candidates: list[GateCandidate],
    filters: HardFilters | None,
    run: PipelineRun,
) -> list[GateCandidate]:
    """Drop hard-filter-blocked candidates and count the drops.

    Args:
        candidates: Loaded gate candidates (job + gate state).
        filters: Hard filter config, or None for no drops.
        run: Pipeline run whose ``jobs_filtered`` counter is incremented.

    Returns:
        Candidates that pass every hard filter (input order preserved).
    """
    if filters is None:
        return candidates
    kept = [c for c in candidates if apply_hard_filters(c.job, filters) is None]
    run.jobs_filtered += len(candidates) - len(kept)
    return kept


def _representatives(candidates: list[GateCandidate]) -> list[GateCandidate]:
    """Reduce candidates to one representative per twin cluster, keeping state.

    ``pick_representatives`` operates on ``JobPosting`` and returns the winning
    member objects; we re-attach each winner's ``GateCandidate`` (carrying its
    persisted ``ml_gate_result``) by store id so dedupe never loses gate state.
    """
    by_id = {_job_id(c.job): c for c in candidates}
    reps = pick_representatives([c.job for c in candidates])
    return [by_id[_job_id(job)] for job in reps]


async def _gate_representatives(
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    representatives: list[GateCandidate],
    dry_run: bool,
) -> list[JobPosting]:
    """Gate only NULL-gate reps and return the surviving job postings.

    Already-'pass' representatives (e.g. a crash between gate-write and Stage-A
    claim) become survivors directly WITHOUT re-gating or re-persisting, per plan
    Decision 8 — re-gating them risks a swapped model / changed
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
    newly_passed = await _gate_unrated(deps, gate, run, to_gate, dry_run)
    return already_pass + newly_passed


async def _gate_unrated(
    deps: EvaluateDependencies,
    gate: MLGate,
    run: PipelineRun,
    to_gate: list[JobPosting],
    dry_run: bool,
) -> list[JobPosting]:
    """Score NULL-gate reps, persist results, and return the 'pass' subset.

    Increments ``run.jobs_ml_gated`` by the non-pass count; persists every fresh
    result (no-op in dry-run).
    """
    if not to_gate:
        return []
    inputs = [
        GateInput(job_id=_job_id(job), title=job.title, jd_text=job.jd_text or "")
        for job in to_gate
    ]
    results = await gate.predict_batch(inputs)
    await _persist_gate_results(deps, to_gate, results, dry_run)
    run.jobs_ml_gated += sum(1 for result in results if result.result != "pass")
    return [
        job
        for job, result in zip(to_gate, results, strict=True)
        if result.result == "pass"
    ]


async def _persist_gate_results(
    deps: EvaluateDependencies,
    representatives: list[JobPosting],
    results: list[MLGateResult],
    dry_run: bool,
) -> None:
    """Persist each gate result concurrently (``results`` align 1:1 with reps).

    No-op in dry-run.
    """
    if dry_run:
        return
    pairs = list(zip(representatives, results, strict=True))

    async def _save(job: JobPosting, result: MLGateResult) -> None:
        await deps.store.save_ml_gate_result(_job_id(job), result)

    await asyncio.gather(*(_save(job, result) for job, result in pairs))


def _limit_survivors(
    survivors: list[JobPosting], limit: int | None
) -> list[JobPosting]:
    """Slice survivors to the Stage A claim ``limit`` in claim order.

    Mirrors the claim query's ``ORDER BY jobs.discovered_at DESC, jobs.id DESC``
    + ``LIMIT`` so the dry-run preview matches exactly what a real run would
    claim. ONLY ``None`` means unsliced (return every survivor); a non-positive
    ``limit`` means "claim nothing" and returns an empty list — matching the
    CLI's "max jobs" contract where 0 takes zero jobs, not unlimited.
    """
    if limit is None:
        return survivors
    if limit <= 0:
        return []
    ordered = sorted(survivors, key=_claim_order_key)
    return ordered[:limit]


def _claim_order_key(job: JobPosting) -> tuple[float, int]:
    """Return the claim-query ordering key (newest first, then highest id).

    Mirrors the SQL ``ORDER BY jobs.discovered_at DESC, jobs.id DESC`` (smaller
    tuple sorts first). Store ids are a numeric ``bigint`` column, so the id
    tiebreak negates the integer value; a non-numeric id (test doubles only) falls
    back to ``0``, keeping the key total and crash-free without affecting real runs.
    """
    job_id = _job_id(job)
    id_rank = -int(job_id) if job_id.isdigit() else 0
    return (-job.discovered_at.timestamp(), id_rank)


def _preview(survivors: list[JobPosting]) -> list[DryRunPreviewItem]:
    """Shape survivors into one ``stage_a`` dry-run preview item each."""
    return [
        DryRunPreviewItem(
            stage="stage_a",
            job_id=job.id,
            title=job.title,
            company=job.company,
        )
        for job in survivors
    ]


def _job_id(job: JobPosting) -> str:
    """Return the store-assigned id, raising if a funnel job lacks one."""
    if job.id is None:
        raise ValueError("funnel candidates must have a store id")
    return job.id


__all__ = ["run_funnel"]
