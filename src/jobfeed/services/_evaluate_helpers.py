"""Private helpers for the EvaluateService (budget, usage, loaders)."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.domain.models import (
    JobPosting,
    LLMResponse,
    LLMUsage,
)
from jobfeed.domain.types import StageName
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin

SHORT_JD_THRESHOLD = 200


def require_job_id(job: JobPosting) -> str:
    """Extract the required store identity from a job posting.

    Args:
        job: Job posting that must have a store-assigned id.

    Returns:
        Store-assigned job identity.

    Raises:
        ValueError: If the job has no id.
    """
    if job.id is None:
        raise ValueError("evaluated jobs must have a store id")
    return job.id


async def check_budget(
    store_ops: StoreOpsMixin,
    max_calls: int,
    max_cost: float,
    logger: JobfeedLogger,
) -> bool:
    """Returns True if budget allows more calls. False to stop.

    Best-effort under concurrency -- up to max_concurrent extra
    calls may slip through before the gate trips (all N workers
    can read the same call count simultaneously).

    Args:
        store_ops: Store operations port for cost queries.
        max_calls: Daily call limit.
        max_cost: Daily cost limit in USD.
        logger: Logger for budget warnings.

    Returns:
        True if budget is available.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    cost = await store_ops.get_cost(today)
    if cost and cost.calls >= max_calls:
        logger.warning("daily_call_limit_reached", calls=cost.calls)
        return False
    if cost and cost.spent_usd >= max_cost:
        logger.warning("daily_cost_limit_reached", spent=cost.spent_usd)
        return False
    return True


async def record_usage(
    store_ops: StoreOpsMixin,
    resp: LLMResponse,
    job_id: str,
    stage: str,
    run_id: str,
) -> None:
    """Record cost and LLM usage after a successful LLM response.

    Args:
        store_ops: Store operations port.
        resp: LLM response with usage metadata.
        job_id: Store-assigned job identity.
        stage: Evaluation stage name.
        run_id: Pipeline run identity.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    await store_ops.record_cost(day=today, spent_usd=resp.cost_usd or 0.0)
    await store_ops.record_llm_usage(
        LLMUsage(
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=resp.cost_usd or 0.0,
            cached=resp.cached,
            latency_ms=resp.latency_ms,
            timestamp=datetime.now(UTC),
            job_id=job_id,
            stage=stage,
            run_id=run_id,
        ),
    )


async def load_stage_a(
    store: JobStore,
    corpus: str,
    limit: int,
    max_days: int | None,
) -> list[JobPosting]:
    """Load pending Stage A jobs with quality band filter.

    Args:
        store: Job store port.
        corpus: Corpus filter value.
        limit: Max jobs to load.
        max_days: Freshness filter.

    Returns:
        List of pending Stage A jobs.
    """
    return await store.load_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )


def log_dry_run(
    logger: JobfeedLogger,
    stage: StageName,
    jobs: list[JobPosting],
) -> None:
    """Log dry-run preview for a stage.

    Args:
        logger: Structured logger.
        stage: Stage name for log context.
        jobs: Jobs that would be evaluated.
    """
    for job in jobs:
        logger.info(
            "evaluate_dry_run_job",
            stage=stage,
            job_id=job.id,
            title=job.title,
            company=job.company,
        )


__all__ = [
    "SHORT_JD_THRESHOLD",
    "check_budget",
    "load_stage_a",
    "log_dry_run",
    "record_usage",
    "require_job_id",
]
