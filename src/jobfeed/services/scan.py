"""Scan service that persists jobs from configured source ports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from jobfeed.domain.errors import SourceBusyError
from jobfeed.domain.models import JobPosting, PipelineRun
from jobfeed.observability import JobfeedLogger, bind_run_id, get_tracer
from jobfeed.ports.source import EnrichResult, ScanSession, SessionSource, SimpleSource
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_perf import StorePerfMixin
from jobfeed.services._timing import StepTimer
from jobfeed.services.error_handler import ServiceErrorHandler
from jobfeed.services.runs import start_pipeline_run

ProgressCallback = Callable[[PipelineRun], None]

SourcePort = SimpleSource | SessionSource
SourceSpec = tuple[str, SourcePort, dict[str, object]]
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

    async def run(
        self,
        sources: list[SourceSpec],
        on_progress: ProgressCallback | None = None,
    ) -> PipelineRun:
        """Fetch jobs from sources and persist scan counters.

        Args:
            sources: Source name, source port, and source config tuples.
            on_progress: Optional callback invoked after each source completes.

        Returns:
            Recorded pipeline run with discovery and upsert counters.
        """
        run = start_pipeline_run(_run_source_name(sources))
        bind_run_id(run.run_id)
        self._tracer = get_tracer("jobfeed.scan")
        self._perf_store = cast(StorePerfMixin, self.store)
        self._on_progress = on_progress
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
        source: SourcePort,
        config: dict[str, object],
    ) -> None:
        async with StepTimer(
            self._perf_store,
            run.run_id,
            "source_fetch",
            name,
            self._tracer,
        ):
            if isinstance(source, SessionSource):
                await self._scan_session_source(run, name, source, config)
            else:
                await self._scan_simple_source(run, name, source, config)
        if self._on_progress is not None:
            self._on_progress(run)

    async def _scan_simple_source(
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
        await self._record_jobs(run, name, jobs)

    async def _scan_session_source(
        self,
        run: PipelineRun,
        name: str,
        source: SessionSource,
        config: dict[str, object],
    ) -> None:
        try:
            jobs = await self._run_session(name, source, config)
        except SourceBusyError as exc:
            # Contention (e.g. another LinkedIn session holds the enrich lock) is
            # a benign skip, not a fetch failure: do not count it as an error.
            self.logger.info("scan_source_busy", source=name, reason=str(exc))
            return
        except Exception as exc:
            self.error_handler.handle_source_fetch_error(run, name, exc)
            return
        await self._record_jobs(run, name, jobs)

    async def _run_session(
        self,
        name: str,
        source: SessionSource,
        config: dict[str, object],
    ) -> list[JobPosting]:
        # ONE locked session spans discover + enrich, so the source's exclusive
        # resource (lock + browser) is held across both phases.
        async with source.session() as session:
            discovered = await session.discover(config)
            if discovered.needs_reauth:
                raise RuntimeError(discovered.error or "source requires reauth")
            return await self._enrich_postings(name, session, discovered.postings)

    async def _enrich_postings(
        self,
        name: str,
        session: ScanSession,
        postings: list[JobPosting],
    ) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for posting in postings:
            result = await session.enrich(posting)
            if result.error is not None:
                self.logger.error(
                    "scan_posting_enrich_failed",
                    source=name,
                    canonical_id=posting.canonical_id,
                    error=result.error,
                )
            jobs.append(_merge_enrichment(posting, result))
        return jobs

    async def _record_jobs(
        self,
        run: PipelineRun,
        name: str,
        jobs: list[JobPosting],
    ) -> None:
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


def _merge_enrichment(posting: JobPosting, result: EnrichResult) -> JobPosting:
    enriched_at = posting.enriched_at
    if result.error is None:
        enriched_at = datetime.now(UTC)
    return replace(
        posting,
        jd_text=result.jd_text,
        jd_quality=result.quality,
        posted_at=result.posted_at or posting.posted_at,
        enriched_at=enriched_at,
        enrich_source=result.enrich_source,
    )


def _run_source_name(sources: list[SourceSpec]) -> str:
    if len(sources) == SINGLE_SOURCE_COUNT:
        return sources[0][0]
    return "scan"


__all__ = ["ScanService", "SourceSpec"]
