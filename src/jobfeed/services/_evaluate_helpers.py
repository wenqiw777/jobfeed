"""Private helpers for the EvaluateService (budget, usage, loaders)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from jobfeed.domain.models import (
    DryRunPreviewItem,
    JobPosting,
    LLMResponse,
    LLMUsage,
    PipelineRun,
)
from jobfeed.domain.types import StageName
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ext import StoreEvaluationBatchMixin, StoreStageBPreviewMixin
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services._evaluate_funnel import run_funnel
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig

SHORT_JD_THRESHOLD = 200


@dataclass(frozen=True)
class UsageRecordContext:
    """Persistence context for one successful LLM usage row."""

    job_id: str
    stage: Literal["a", "b"]
    run_id: str
    ledger_day: str


@dataclass(frozen=True)
class DryRunRequest:
    """Loose run params for an evaluate dry-run preview.

    ``deps`` / ``config`` / ``run`` are passed alongside (not stored) so this
    stays a small value object the CLI-facing service can build inline.
    """

    logger: JobfeedLogger
    stage: str
    corpus: str
    limit: int
    max_days: int | None


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
    calls = cost.calls if cost else 0
    spent_usd = cost.spent_usd if cost else 0.0
    if calls >= max_calls:
        logger.warning("daily_call_limit_reached", calls=calls)
        return False
    if spent_usd >= max_cost:
        logger.warning("daily_cost_limit_reached", spent=spent_usd)
        return False
    return True


async def record_call_attempt(store_ops: StoreOpsMixin) -> str:
    """Reserve one daily LLM call attempt before invoking an external model.

    Args:
        store_ops: Store operations port.

    Returns:
        UTC ledger day used for the reservation and later spend recording.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    await store_ops.record_cost(day=today, spent_usd=0.0, calls=1)
    return today


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


async def build_dry_run_preview(
    deps: EvaluateDependencies,
    config: EvaluateRuntimeConfig,
    run: PipelineRun,
    request: DryRunRequest,
) -> list[DryRunPreviewItem]:
    """Build the jobs a dry-run would evaluate: Stage A funnel + Stage B preview.

    Stage A survivors come from the unconditional funnel (load -> hard-filter ->
    dedupe -> optional gate, persisting nothing in dry-run); ``run_funnel`` writes
    them onto ``run.dry_run_preview``, sliced to the Stage A ``limit`` in claim
    order so the preview matches what a real run would actually claim. Stage B
    preview rows are appended after, so non-representative twins and gate failures
    never appear.

    Args:
        deps: Evaluate dependencies (store, optional ml_gate / hard_filters).
        config: Runtime config (ml_gate flag + Stage A threshold).
        run: Pipeline run whose preview list is populated in place.
        request: Loose dry-run params (stage / corpus / limit / max_days).

    Returns:
        Stable preview items suitable for CLI output.
    """
    # ``--limit 0`` means "max jobs"=0: claim/score nothing. Short-circuit the
    # Stage-A funnel BEFORE loading candidates or gating, mirroring the real-run
    # guard (``_run_stage_a`` early-returns on ``limit <= 0``). Without this, the
    # funnel would load candidates and call the ML gate's ``predict_batch`` (and
    # thus the heavy embedder) only to slice the preview to ``[]`` afterwards.
    if request.stage != "b" and request.limit > 0:
        await run_funnel(
            deps,
            config,
            run,
            request.corpus,
            request.max_days,
            logger=request.logger,
            dry_run=True,
            limit=request.limit,
        )
    if request.stage != "a":
        jobs_b = await load_stage_b_dry_run(
            deps.store, request.limit, request.max_days, config.stage_a_threshold
        )
        run.dry_run_preview.extend(log_dry_run(request.logger, "stage_b", jobs_b))
    return run.dry_run_preview


async def load_stage_b_dry_run(
    store: JobStore,
    limit: int,
    max_days: int | None,
    threshold: int,
) -> list[JobPosting]:
    """Preview Stage B work with the same threshold semantics as a real run.

    Args:
        store: Job store port.
        limit: Max jobs to preview.
        max_days: Freshness filter.
        threshold: Active Stage A threshold.

    Returns:
        Jobs that would enter Stage B without mutating skipped statuses.
    """
    if isinstance(store, StoreStageBPreviewMixin):
        return await store.preview_pending_stage_b_after_threshold_sync(
            limit=limit,
            max_days=max_days,
            stage_a_threshold=threshold,
        )
    return await store.load_pending_stage_b(
        limit=limit,
        max_days=max_days,
        stage_a_threshold=threshold,
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


def log_dry_run(
    logger: JobfeedLogger,
    stage: StageName,
    jobs: list[JobPosting],
) -> list[DryRunPreviewItem]:
    """Log dry-run preview for a stage.

    Args:
        logger: Structured logger.
        stage: Stage name for log context.
        jobs: Jobs that would be evaluated.
    Returns:
        Stable preview items suitable for CLI output.
    """
    preview: list[DryRunPreviewItem] = []
    for job in jobs:
        logger.info(
            "evaluate_dry_run_job",
            stage=stage,
            job_id=job.id,
            title=job.title,
            company=job.company,
        )
        preview.append(
            DryRunPreviewItem(
                stage=stage,
                job_id=job.id,
                title=job.title,
                company=job.company,
            )
        )
    return preview


__all__ = [
    "SHORT_JD_THRESHOLD",
    "DryRunRequest",
    "UsageRecordContext",
    "build_dry_run_preview",
    "check_budget",
    "load_stage_a_scores",
    "load_stage_b_dry_run",
    "log_dry_run",
    "record_call_attempt",
    "record_usage",
    "require_job_id",
]
