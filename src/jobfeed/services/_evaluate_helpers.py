"""Shared scoring-loop helpers for the EvaluateService.

Small stateless utilities the Stage A / Stage B scoring loops call directly:
the store-id guard, the short-JD threshold, per-call usage recording, and Stage
A score loading for Stage B prompt context. (Budget reservation lives in
``_evaluate_budget``; dry-run preview assembly in ``_evaluate_dryrun``.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from jobfeed.domain.models import JobPosting, LLMResponse, LLMUsage, PipelineRun
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ext import StoreEvaluationBatchMixin
from jobfeed.ports.store_ops import StoreOpsMixin

if TYPE_CHECKING:
    from jobfeed.observability import JobfeedLogger
    from jobfeed.services.evaluate_types import (
        EvaluateDependencies,
        EvaluateRuntimeConfig,
    )

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


async def finalize_evaluate_run(
    store: JobStore,
    run: PipelineRun,
    dry_run: bool,
    on_progress: Callable[[PipelineRun], None] | None,
) -> None:
    """Tally scores, persist final status, and fire the final progress event.

    Args:
        store: Job store for status persistence.
        run: Pipeline run being finalized.
        dry_run: True to skip store writes.
        on_progress: Optional callback fired with the finalized run.
    """
    run.jobs_scored = run.stage_a_scored + run.stage_b_scored
    run.finished_at = datetime.now(UTC)
    run.status = "succeeded"
    if not dry_run:
        await store.update_pipeline_run_status(run)
    if on_progress is not None:
        on_progress(run)


async def mark_evaluate_run_failed(store: JobStore, run: PipelineRun) -> None:
    """Persist a failed terminal status so the run is not left 'running'.

    Args:
        store: Job store for status persistence.
        run: Pipeline run whose stage work raised.
    """
    run.status = "failed"
    run.finished_at = datetime.now(UTC)
    await store.update_pipeline_run_status(run)


async def run_auto_decay(
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    logger: JobfeedLogger,
) -> None:
    """Ghost/archive stale jobs before evaluation; log a non-empty sweep.

    Args:
        deps: Evaluate dependencies exposing the status store.
        config: Runtime config with the decay thresholds.
        logger: Structured logger for the sweep event.
    """
    decay = await deps.store_status.auto_decay(
        ghost_days=config.ghost_days,
        archive_ignored_days=config.archive_ignored_days,
    )
    if decay.ghosted or decay.archived:
        logger.info("auto_decay_sweep", ghosted=decay.ghosted, archived=decay.archived)


__all__ = [
    "SHORT_JD_THRESHOLD",
    "UsageRecordContext",
    "finalize_evaluate_run",
    "load_stage_a_scores",
    "mark_evaluate_run_failed",
    "record_usage",
    "require_job_id",
    "run_auto_decay",
]
