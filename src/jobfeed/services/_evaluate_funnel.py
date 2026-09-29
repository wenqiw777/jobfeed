"""Non-claiming evaluation funnel: load -> hard-filter -> distinct posts -> gate.

The funnel turns eligible Stage A candidates into a survivor job-id list without
claiming anything. Filtering and unique job IDs are unconditional; gating depends on
``config.ml_gate_enabled`` AND a wired ``deps.ml_gate``. The caller
(``EvaluateService``) then claims exactly the survivor ids via
``claim_stage_a_by_ids``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from jobfeed.domain.filtering import HardFilters, apply_hard_filters
from jobfeed.domain.models import (
    DryRunPreviewItem,
    JobPosting,
    PipelineRun,
)
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services._evaluate_claims import load_gate_candidates_for_run
from jobfeed.services._evaluate_gate import gate_representatives, resolve_gate_mode
from jobfeed.services._evaluate_repost import eligible_jobs
from jobfeed.services._evaluate_seniority import apply_seniority_gate
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
    job_ids: list[str] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> list[str]:
    """Run the candidate funnel and return survivor Stage A job-ids.

    Flow: page candidates (no claim) -> hard filter (count drops), retaining each
    distinct job ID, overfetching past filtered rows until enough eligible
    posts or the eligible set is exhausted -> optional ML gate (gate
    only NULL-gate reps; persist + count non-pass; already-'pass' reps survive
    without re-gating). Survivors are the hard-filter ∩ gate-pass
    set (or, gate off, hard-filter survivors). In dry-run nothing is
    persisted; survivors are sliced to the Stage A ``limit`` (matching a real
    claim) and a survivor preview is recorded on ``run.dry_run_preview``.

    Args:
        deps: Evaluate dependencies (store, optional ml_gate / hard_filters).
        config: Runtime config (ml_gate flag + gate-candidate budget / page size).
        run: Pipeline run whose counters are mutated in place.
        corpus: Corpus filter value.
        max_days: Freshness filter on discovered_at.
        logger: Run logger; warns if the candidate scan is page-bound truncated.
        dry_run: When True, persist nothing and record a (limit-sliced) preview.
        limit: Stage A claim limit; slices the dry-run preview to match a real
            run (``None`` leaves it unsliced).
        on_progress: Optional bounded callback for live ML-gate progress.

    Returns:
        Survivor job-ids to hand to Stage A (empty in dry-run).
    """
    gate_mode = await resolve_gate_mode(deps, config, dry_run=dry_run)
    representatives = await _load_representatives(
        deps,
        config,
        run,
        corpus,
        max_days,
        logger,
        job_ids=job_ids,
        exclude_gate_failed=gate_mode == "filter",
        persist_hard_filters=not dry_run,
    )
    survivors = await gate_representatives(
        deps,
        config,
        run,
        representatives,
        dry_run,
        mode=gate_mode,
        on_progress=on_progress,
    )
    survivors = await eligible_jobs(deps.store, survivors)
    if deps.seniority_gate is not None:
        survivors, seniority_blocked = await apply_seniority_gate(
            deps.seniority_gate,
            survivors,
            mode=config.seniority_gate_mode,
        )
        run.jobs_seniority_filtered += seniority_blocked
    if dry_run:
        # Append (not assign) so a later Stage-B preview pass keeps this pass's
        # items; matches build_dry_run_preview's Stage-B ``.extend`` and is
        # identical today (the list starts empty and Stage A runs first).
        run.dry_run_preview.extend(_preview(_limit_survivors(survivors, limit)))
        return []
    return [_job_id(job) for job in survivors]


async def _load_representatives(  # noqa: PLR0913 - paginated load inputs
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    corpus: str,
    max_days: int | None,
    logger: JobfeedLogger,
    *,
    job_ids: list[str] | None,
    exclude_gate_failed: bool,
    persist_hard_filters: bool,
) -> list[GateCandidate]:
    """Page and hard-filter until enough distinct job IDs survive.

    ``ml_gate_max_candidates`` is both the page size and target. Page past
    hard-filtered rows using the stable ``(discovered_at, id)`` cursor. Repeated
    IDs are eliminated, but different posts are never folded by company/title,
    content or external identity. Stop on the target, source exhaustion, or
    the explicit page safety bound (which emits a warning).

    Returns:
        Distinct posts capped to ``ml_gate_max_candidates`` in newest-first
        order, each carrying its persisted ``ml_gate_result``.
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
            exclude_gate_failed=exclude_gate_failed,
            after=after,
            job_ids=job_ids,
        )
        survivors.extend(
            await _apply_hard_filters(
                page,
                deps.hard_filters,
                run,
                store=deps.store,
                persist=persist_hard_filters,
            )
        )
        representatives = _representatives(survivors)
        if len(representatives) >= page_size or len(page) < page_size:
            # Target met or eligible set exhausted; cap to the gate budget (a
            # full final page can overshoot the target).
            return representatives[:page_size]
        last = page[-1].job
        after = (last.discovered_at, int(_job_id(last)))
    logger.warning("funnel_candidate_scan_truncated", max_pages=_MAX_CANDIDATE_PAGES)
    return _representatives(survivors)[:page_size]


async def _apply_hard_filters(
    candidates: list[GateCandidate],
    filters: HardFilters | None,
    run: PipelineRun,
    *,
    store: JobStore,
    persist: bool,
) -> list[GateCandidate]:
    """Drop hard-filter-blocked candidates and count the drops.

    Args:
        candidates: Loaded gate candidates (job + gate state).
        filters: Hard filter config, or None for no drops.
        run: Pipeline run whose ``jobs_filtered`` counter is incremented.

    Returns:
        Candidates passing every hard filter (input order preserved).
    """
    if filters is None:
        filters = HardFilters()
    kept: list[GateCandidate] = []
    rejected: dict[str, str] = {}
    for candidate in candidates:
        reason = apply_hard_filters(candidate.job, filters)
        if reason is None:
            kept.append(candidate)
            continue
        rejected[_job_id(candidate.job)] = reason
    if persist and rejected:
        await store.save_hard_filters(rejected)
    run.jobs_filtered += len(rejected)
    return kept


def _representatives(candidates: list[GateCandidate]) -> list[GateCandidate]:
    """Retain every distinct post; exact request reuse owns scoring savings."""
    by_id = {_job_id(c.job): c for c in candidates}
    return list(by_id.values())


def _limit_survivors(
    survivors: list[JobPosting], limit: int | None
) -> list[JobPosting]:
    """Slice survivors to the Stage A claim ``limit`` in claim order.

    Mirrors the claim query's ``ORDER BY jobs.discovered_at DESC, jobs.id DESC``
    + ``LIMIT`` so the dry-run preview matches what a real run would claim. ONLY
    ``None`` is unsliced; a non-positive ``limit`` returns an empty list —
    matching the CLI's "max jobs" contract where 0 takes zero jobs, not unlimited.
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
    tuple sorts first). Store ids are a numeric ``bigint``, so the id tiebreak
    negates the integer; a non-numeric id (test doubles only) falls back to ``0``,
    keeping the key total and crash-free without affecting real runs.
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
