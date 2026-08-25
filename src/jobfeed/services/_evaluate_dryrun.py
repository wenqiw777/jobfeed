"""Read-only preview for the unified evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from jobfeed.domain.models import DryRunPreviewItem, PipelineRun
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_unified import StoreUnifiedEvaluationMixin
from jobfeed.services._evaluate_unified import EVALUATOR_VERSION
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig


@dataclass(frozen=True)
class DryRunRequest:
    """Loose filters supplied by CLI or web preview callers."""

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
    """Preview exactly the jobs the unified evaluator would claim.

    Args:
        deps: Service dependencies containing the canonical store.
        config: Runtime config retained for the stable service signature.
        run: Preview run populated in place.
        request: Corpus, limit, and freshness filters.

    Returns:
        Stable preview rows without claims, usage, or evaluation writes.
    """
    del config
    if request.limit <= 0:
        return run.dry_run_preview
    store = cast(StoreUnifiedEvaluationMixin, deps.store)
    jobs = await store.preview_pending_evaluations(
        evaluator_version=EVALUATOR_VERSION,
        corpus=request.corpus,
        limit=request.limit,
        max_days=request.max_days,
    )
    for job in jobs:
        request.logger.info(
            "evaluate_dry_run_job",
            stage="evaluation",
            job_id=job.id,
            title=job.title,
            company=job.company,
        )
        run.dry_run_preview.append(
            DryRunPreviewItem(
                stage="evaluation",
                job_id=job.id,
                title=job.title,
                company=job.company,
            )
        )
    run.jobs_scored = len(run.dry_run_preview)
    return run.dry_run_preview


__all__ = ["DryRunRequest", "build_dry_run_preview"]
