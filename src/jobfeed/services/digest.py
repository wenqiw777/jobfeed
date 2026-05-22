"""Digest service that renders Markdown from evaluated jobs."""

from __future__ import annotations

from datetime import datetime

from jobfeed.domain.digest import render_digest
from jobfeed.domain.models import JobEvaluation
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore


class DigestService:
    """Application service for rendering the current job digest."""

    def __init__(self, store: JobStore, logger: JobfeedLogger) -> None:
        """Create a digest service with injected ports.

        Args:
            store: Persistence port used to load evaluated jobs.
            logger: Structured logger for digest events.
        """
        self.store = store
        self.logger = logger

    async def run(self, cutoff_at: datetime | None = None) -> str:
        """Render a Markdown digest from stored evaluations.

        Args:
            cutoff_at: Optional boundary for new vs previously seen apply jobs.

        Returns:
            Markdown digest.
        """
        evaluations = await self.store.list_evaluated_jobs()
        stats = _stats_for(evaluations)
        digest = render_digest(evaluations, stats, cutoff_at=cutoff_at)
        self.logger.info(
            "digest_rendered",
            total_jobs=stats["total_jobs"],
            stage_b_evaluated=stats["stage_b_evaluated"],
        )
        return digest


def _stats_for(evaluations: list[JobEvaluation]) -> dict[str, object]:
    stage_a_count = sum(1 for item in evaluations if item.stage_a is not None)
    stage_b_count = sum(1 for item in evaluations if item.stage_b is not None)
    # TODO(phase1): Replace placeholder scan/filter counters with store-backed
    # run statistics once historical pipeline run queries exist.
    return {
        "total_jobs": len(evaluations),
        "scraped_today": len(evaluations),
        "llm_calls_today": stage_a_count + stage_b_count,
        "stage_b_evaluated": stage_b_count,
        "filtered_count": 0,
    }


__all__ = ["DigestService"]
