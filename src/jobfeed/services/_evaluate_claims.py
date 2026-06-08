"""Pending evaluation claim helpers for EvaluateService."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from jobfeed.domain.models import JobPosting
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_claims import GateCandidate, StoreEvaluationClaimMixin
from jobfeed.ports.store_ext import StoreEvaluationBatchMixin

VALID_EVALUATE_STAGES = frozenset({"a", "b", "both"})
STAGE_A_QUALITY_BANDS = frozenset({"full", "good"})
STAGE_B_LEASE_HEARTBEAT_SECONDS = 1800.0


def validate_evaluate_stage(stage: str) -> None:
    """Reject invalid evaluate stage values before any LLM calls.

    Args:
        stage: Requested stage selector.

    Raises:
        ValueError: If stage is not a recognized selector.
    """
    if stage not in VALID_EVALUATE_STAGES:
        raise ValueError(f"unknown evaluate stage: {stage!r}")


async def load_gate_candidates_for_run(  # noqa: PLR0913 - distinct load filters + keyset cursor
    store: JobStore,
    corpus: str,
    limit: int,
    max_days: int | None,
    *,
    exclude_gate_failed: bool,
    after: tuple[datetime, int] | None = None,
) -> list[GateCandidate]:
    """Load one page of Stage A gate candidates without claiming, for the funnel.

    Args:
        store: Job store, optionally with gate-candidate support.
        corpus: Corpus filter value.
        limit: Max candidates to load (one page).
        max_days: Freshness filter.
        exclude_gate_failed: When True, drop rows whose gate result is 'fail'.
        after: Optional ``(discovered_at, id)`` keyset cursor for the next page;
            ignored on legacy stores without gate-candidate support.

    Returns:
        Gate candidates (job + persisted ``ml_gate_result``) pending Stage A
        (no lease mutation). Legacy stores without gate support surface a
        ``None`` gate result.
    """
    if isinstance(store, StoreEvaluationClaimMixin):
        return await store.load_gate_candidates(
            corpus=corpus,
            quality_bands=STAGE_A_QUALITY_BANDS,
            max_days=max_days,
            limit=limit,
            exclude_gate_failed=exclude_gate_failed,
            after=after,
        )
    jobs = await store.load_pending_stage_a(
        quality_bands=STAGE_A_QUALITY_BANDS,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )
    return [GateCandidate(job=job, ml_gate_result=None) for job in jobs]


async def load_stage_a_for_run(
    store: JobStore,
    corpus: str,
    limit: int,
    max_days: int | None,
    survivor_ids: list[str] | None = None,
) -> list[JobPosting]:
    """Claim or load Stage A jobs for a real evaluation run.

    When ``survivor_ids`` is provided (the Phase 5 funnel hand-off), the claim is
    restricted to exactly those ids via ``claim_stage_a_by_ids``; an empty list
    claims nothing. When ``None`` (legacy path), the broad pending claim is used.

    Args:
        store: Job store, optionally with atomic claim support.
        corpus: Corpus filter value.
        limit: Max jobs to load.
        max_days: Freshness filter.
        survivor_ids: Funnel survivor ids to restrict the claim to, or None.

    Returns:
        Stage A jobs assigned to this run.
    """
    if survivor_ids is not None:
        return await _claim_stage_a_by_ids_for_run(
            store, survivor_ids, corpus, limit, max_days
        )
    if isinstance(store, StoreEvaluationClaimMixin):
        return await store.claim_pending_stage_a(
            quality_bands=STAGE_A_QUALITY_BANDS,
            corpus=corpus,
            limit=limit,
            max_days=max_days,
        )
    return await store.load_pending_stage_a(
        quality_bands=STAGE_A_QUALITY_BANDS,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )


async def _claim_stage_a_by_ids_for_run(
    store: JobStore,
    survivor_ids: list[str],
    corpus: str,
    limit: int,
    max_days: int | None,
) -> list[JobPosting]:
    """Claim Stage A jobs restricted to the funnel survivor id set.

    Args:
        store: Job store, optionally with atomic claim support.
        survivor_ids: Survivor ids to claim from (empty => claim nothing).
        corpus: Corpus filter value.
        limit: Max jobs to load.
        max_days: Freshness filter.

    Returns:
        Claimed Stage A jobs (a subset of ``survivor_ids``).
    """
    if not survivor_ids:
        return []
    if isinstance(store, StoreEvaluationClaimMixin):
        return await store.claim_stage_a_by_ids(
            survivor_ids,
            quality_bands=STAGE_A_QUALITY_BANDS,
            corpus=corpus,
            limit=limit,
            max_days=max_days,
        )
    wanted = set(survivor_ids)
    loaded = await store.load_pending_stage_a(
        quality_bands=STAGE_A_QUALITY_BANDS,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )
    # Legacy-path limitation: ``load_pending_stage_a`` returns plain JobPosting
    # rows with no Stage-A status (it lives on the evaluations table, dropped by
    # the loader), and under corpus="all" the query applies no status predicate.
    # So a previously-'completed' survivor cannot be excluded here without a port
    # change. PostgresStore takes the StoreEvaluationClaimMixin branch above,
    # whose claim_stage_a_by_ids carries the correct completed-exclusion, so real
    # runs are unaffected; this fallback exists only for mixin-less test doubles.
    return [job for job in loaded if job.id in wanted]


async def load_stage_b_for_run(
    store: JobStore,
    limit: int,
    max_days: int | None,
    threshold: int,
) -> list[JobPosting]:
    """Claim or load Stage B jobs for a real evaluation run.

    Args:
        store: Job store, optionally with atomic claim support.
        limit: Max jobs to load.
        max_days: Freshness filter.
        threshold: Stage A threshold for Stage B eligibility.

    Returns:
        Stage B jobs assigned to this run.
    """
    if isinstance(store, StoreEvaluationClaimMixin):
        return await store.claim_pending_stage_b(
            limit=limit,
            max_days=max_days,
            stage_a_threshold=threshold,
        )
    return await store.load_pending_stage_b(
        limit=limit,
        max_days=max_days,
        stage_a_threshold=threshold,
    )


async def release_stage_a_for_run(store: JobStore, job_id: str) -> None:
    """Release an unspent Stage A claim when no LLM call was made.

    Args:
        store: Job store, optionally with atomic claim support.
        job_id: Claimed job identity.
    """
    if isinstance(store, StoreEvaluationClaimMixin):
        await store.release_stage_a_claim(job_id)


async def release_stage_b_for_run(store: JobStore, job_id: str) -> None:
    """Release an unspent Stage B claim when no LLM call was made.

    Args:
        store: Job store, optionally with atomic claim support.
        job_id: Claimed job identity.
    """
    if isinstance(store, StoreEvaluationClaimMixin):
        await store.release_stage_b_claim(job_id)


async def sync_stage_b_threshold(
    store: JobStore, threshold: int, max_days: int | None
) -> None:
    """Reopen/skip Stage B rows to match the current Stage A threshold.

    Reopens rows now at/above the threshold and marks below-threshold rows
    skipped, scoped to ``max_days`` freshness. A no-op when the store lacks the
    evaluation-batch capability.

    Args:
        store: Job store, optionally with evaluation-batch support.
        threshold: Current Stage A score threshold.
        max_days: Freshness window applied to both batch updates.
    """
    if not isinstance(store, StoreEvaluationBatchMixin):
        return
    await store.reopen_stage_b_at_or_above_threshold(threshold, max_days=max_days)
    await store.mark_stage_b_below_threshold(threshold, max_days=max_days)


@asynccontextmanager
async def maintain_stage_b_claim(
    store: JobStore,
    job_id: str,
) -> AsyncIterator[None]:
    """Keep a Stage B claim fresh while an LLM call is in flight.

    Args:
        store: Job store, optionally with claim lease refresh support.
        job_id: Claimed job identity.

    Returns:
        Async context manager for the protected LLM call.
    """
    if not isinstance(store, StoreEvaluationClaimMixin):
        yield
        return
    await store.refresh_stage_b_claim(job_id)
    heartbeat = asyncio.create_task(_refresh_stage_b_claim_loop(store, job_id))
    try:
        yield
    finally:
        heartbeat.cancel()
        # A background refresh failure must not replace a completed LLM response.
        with suppress(asyncio.CancelledError, Exception):
            await heartbeat


async def _refresh_stage_b_claim_loop(
    store: StoreEvaluationClaimMixin,
    job_id: str,
) -> None:
    while True:
        await asyncio.sleep(STAGE_B_LEASE_HEARTBEAT_SECONDS)
        await store.refresh_stage_b_claim(job_id)
