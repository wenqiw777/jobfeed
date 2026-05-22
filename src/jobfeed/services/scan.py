"""Scan service that persists jobs from configured source ports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from jobfeed.domain.models import JobPosting, PipelineRun
from jobfeed.observability import JobfeedLogger, bind_run_id
from jobfeed.ports.source import SimpleSource
from jobfeed.ports.store import JobStore
from jobfeed.services.error_handler import ServiceErrorHandler
from jobfeed.services.runs import start_pipeline_run

SourceSpec = tuple[str, SimpleSource, dict[str, object]]
SINGLE_SOURCE_COUNT = 1


class ScanService:
    """Application service for source fetch and job persistence."""

    def __init__(self, store: JobStore, logger: JobfeedLogger) -> None:
        """Create a scan service with injected ports.

        Args:
            store: Persistence port used to save jobs and pipeline metrics.
            logger: Structured logger for scan events.
        """
        self.store = store
        self.logger = logger
        self.error_handler = ServiceErrorHandler(store=store, logger=logger)

    async def run(self, sources: list[SourceSpec]) -> PipelineRun:
        """Fetch jobs from sources and persist scan counters.

        Args:
            sources: Source name, source port, and source config tuples.

        Returns:
            Recorded pipeline run with discovery and upsert counters.
        """
        run = start_pipeline_run(_run_source_name(sources))
        bind_run_id(run.run_id)
        await asyncio.gather(
            *(
                self._scan_one_source(run, name, source, config)
                for name, source, config in sources
            )
        )
        run.finished_at = datetime.now(UTC)
        await self.store.record_pipeline_run(run)
        return run

    async def _scan_one_source(
        self,
        run: PipelineRun,
        name: str,
        source: SimpleSource,
        config: dict[str, object],
    ) -> None:
        try:
            jobs = await source.fetch_jobs(config)
        except Exception as exc:
            self.error_handler.handle_source_fetch_error(run, name, exc)
            return
        inserted, updated = await self._save_jobs(jobs)
        run.jobs_discovered += len(jobs)
        run.jobs_inserted += inserted
        run.jobs_updated += updated
        self.logger.info(
            "scan_source_completed",
            source=name,
            jobs_discovered=len(jobs),
            jobs_inserted=inserted,
            jobs_updated=updated,
        )

    async def _save_jobs(self, jobs: list[JobPosting]) -> tuple[int, int]:
        inserted = 0
        updated = 0
        for job in jobs:
            result = await self.store.save_job(job)
            inserted += int(result.inserted)
            updated += int(result.updated)
        return inserted, updated


def _run_source_name(sources: list[SourceSpec]) -> str:
    if len(sources) == SINGLE_SOURCE_COUNT:
        return sources[0][0]
    return "scan"


__all__ = ["ScanService", "SourceSpec"]
