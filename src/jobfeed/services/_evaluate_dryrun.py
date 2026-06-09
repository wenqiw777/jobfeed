"""Dry-run preview assembly for the EvaluateService.

A dry run reports the jobs a real run would evaluate without making any LLM
calls or persisting scores: the Stage A funnel survivors (load -> hard-filter
-> dedupe -> optional gate, persisting nothing) followed by the Stage B preview
rows. Kept apart from the live scoring loops so the preview path reads on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobfeed.domain.models import DryRunPreviewItem, JobPosting, PipelineRun
from jobfeed.domain.types import StageName
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ext import StoreStageBPreviewMixin
from jobfeed.services._evaluate_funnel import run_funnel
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig


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
    # ``--limit 0`` means "max jobs"=0: claim/score nothing. Short-circuit BOTH
    # stages BEFORE loading candidates or gating, mirroring the real-run guards
    # (``_run_stage_a`` and ``_run_stage_b`` both early-return on ``limit <= 0``).
    # Without this, Stage A would load candidates and call the ML gate's
    # ``predict_batch`` (and thus the heavy embedder), and Stage B would query the
    # store for candidates, only to slice the preview to ``[]`` afterwards.
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
    if request.stage != "a" and request.limit > 0:
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
    "DryRunRequest",
    "build_dry_run_preview",
    "load_stage_b_dry_run",
    "log_dry_run",
]
