"""Shared scoring-loop helpers for the EvaluateService.

Small stateless utilities the Stage A / Stage B scoring loops call directly:
the store-id guard, the short-JD threshold, per-call usage recording, and Stage
A score loading for Stage B prompt context. (Budget reservation lives in
``_evaluate_budget``; dry-run preview assembly in ``_evaluate_dryrun``.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from jobfeed.domain.models import JobPosting, LLMResponse, LLMUsage
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ext import StoreEvaluationBatchMixin
from jobfeed.ports.store_ops import StoreOpsMixin

SHORT_JD_THRESHOLD = 200


@dataclass(frozen=True)
class UsageRecordContext:
    """Persistence context for one successful LLM usage row."""

    job_id: str
    stage: Literal["a", "b"]
    run_id: str
    ledger_day: str


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


async def record_usage(
    store_ops: StoreOpsMixin,
    resp: LLMResponse,
    context: UsageRecordContext,
) -> None:
    """Record cost and LLM usage after a successful LLM response.

    Args:
        store_ops: Store operations port.
        resp: LLM response with usage metadata.
        context: Store and budget metadata for the LLM response.
    """
    await store_ops.record_llm_usage_with_cost(
        day=context.ledger_day,
        spent_usd=resp.cost_usd or 0.0,
        usage=LLMUsage(
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=resp.cost_usd or 0.0,
            cached=resp.cached,
            latency_ms=resp.latency_ms,
            timestamp=datetime.now(UTC),
            job_id=context.job_id,
            stage=context.stage,
            run_id=context.run_id,
        ),
    )


async def load_stage_a_scores(
    store: JobStore,
    jobs: list[JobPosting],
) -> dict[str, int | None]:
    """Load Stage A scores for Stage B prompt context when supported.

    Args:
        store: Job store that may support batch score loading.
        jobs: Stage B candidate jobs.

    Returns:
        Mapping of job id to stored Stage A score.
    """
    if not isinstance(store, StoreEvaluationBatchMixin):
        return {}
    return await store.get_stage_a_scores([require_job_id(job) for job in jobs])


__all__ = [
    "SHORT_JD_THRESHOLD",
    "UsageRecordContext",
    "load_stage_a_scores",
    "record_usage",
    "require_job_id",
]
