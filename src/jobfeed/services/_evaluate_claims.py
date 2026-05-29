"""Pending evaluation claim helpers for EvaluateService."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from jobfeed.domain.models import JobPosting
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_claims import StoreEvaluationClaimMixin

VALID_EVALUATE_STAGES = frozenset({"a", "b", "both"})
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


async def load_stage_a_for_run(
    store: JobStore,
    corpus: str,
    limit: int,
    max_days: int | None,
) -> list[JobPosting]:
    """Claim or load Stage A jobs for a real evaluation run.

    Args:
        store: Job store, optionally with atomic claim support.
        corpus: Corpus filter value.
        limit: Max jobs to load.
        max_days: Freshness filter.

    Returns:
        Stage A jobs assigned to this run.
    """
    if isinstance(store, StoreEvaluationClaimMixin):
        return await store.claim_pending_stage_a(
            quality_bands=frozenset({"full", "good"}),
            corpus=corpus,
            limit=limit,
            max_days=max_days,
        )
    return await store.load_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )


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


async def preview_stage_a_for_run(
    store: JobStore,
    corpus: str,
    limit: int,
    max_days: int | None,
) -> list[JobPosting]:
    """Preview Stage A jobs a real run would claim.

    Args:
        store: Job store, optionally with claim preview support.
        corpus: Corpus filter value.
        limit: Max jobs to preview.
        max_days: Freshness filter.

    Returns:
        Stage A jobs that would be assigned to a real run.
    """
    if isinstance(store, StoreEvaluationClaimMixin):
        return await store.preview_claimable_stage_a(
            quality_bands=frozenset({"full", "good"}),
            corpus=corpus,
            limit=limit,
            max_days=max_days,
        )
    return await store.load_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )


async def _refresh_stage_b_claim_loop(
    store: StoreEvaluationClaimMixin,
    job_id: str,
) -> None:
    while True:
        await asyncio.sleep(STAGE_B_LEASE_HEARTBEAT_SECONDS)
        await store.refresh_stage_b_claim(job_id)
