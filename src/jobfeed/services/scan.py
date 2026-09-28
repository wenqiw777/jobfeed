"""Scan service that persists jobs from configured source ports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any, cast

from jobfeed.domain.errors import RunLeaseLostError, SourceBusyError
from jobfeed.domain.models import JobPosting, PipelineRun, SaveJobResult
from jobfeed.domain.quality import assess_quality
from jobfeed.observability import JobfeedLogger, bind_run_id, get_tracer
from jobfeed.ports.pipeline import PipelineStep, PipelineStore, ScanJournal
from jobfeed.ports.run_leases import RunLeaseStore
from jobfeed.ports.source import (
    EnrichResult,
    PartialSourceFetchError,
    ProgressiveSimpleSource,
    ScanSession,
    SessionSource,
    SimpleSource,
    SourceFetchProgress,
)
from jobfeed.ports.store import JobStore
from jobfeed.services._timing import StepTimer, get_perf_store
from jobfeed.services.error_handler import ServiceErrorHandler
from jobfeed.services.pipeline_context import POSTINGS, current_pipeline
from jobfeed.services.run_orchestration import RunLeaseOrchestrator, RunLeaseSession

ProgressCallback = Callable[[PipelineRun], None]

SourcePort = SimpleSource | SessionSource
SourceSpec = tuple[str, SourcePort, dict[str, object]]
SINGLE_SOURCE_COUNT = 1
_SAVE_PROGRESS_INTERVAL = 100


class ScanService:
    """Application service for source fetch and job persistence."""

    def __init__(
        self,
        store: JobStore,
        logger: JobfeedLogger,
        run_orchestrator: RunLeaseOrchestrator | None = None,
        *,
        journal: ScanJournal | None = None,
    ) -> None:
        """Create a scan service with injected ports.

        Args:
            store: Persistence port used to save jobs and pipeline metrics.
            logger: Structured logger for scan events.
        """
        self.store = store
        self._journal = journal
        self._source_write_generations: dict[str, str | None] = {}
        self.logger = logger
        self.error_handler = ServiceErrorHandler(store=store, logger=logger)
        self._run_orchestrator = run_orchestrator or RunLeaseOrchestrator(
            cast(RunLeaseStore, store)
        )

    async def run(
        self,
        sources: list[SourceSpec],
        on_progress: ProgressCallback | None = None,
        lease_session: RunLeaseSession | None = None,
    ) -> PipelineRun:
        """Fetch jobs from sources and persist scan counters.

        Args:
            sources: Source name, source port, and source config tuples.
            on_progress: Optional callback invoked after each source completes.
            lease_session: Pre-acquired web-run fence; direct calls acquire one.

        Returns:
            Recorded pipeline run with discovery and upsert counters.
        Raises: Whatever escaped the scan, after the run is marked failed.
        """
        if lease_session is None:
            session = await self._run_orchestrator.start(
                "scan", run_source_name(sources)
            )
            try:
                return await self._run_orchestrator.execute(
                    session,
                    lambda active: self._run_leased(
                        active, sources, on_progress=on_progress
                    ),
                )
            finally:
                await self.release_completed_pipeline(session.run)
        await self._run_leased(lease_session, sources, on_progress=on_progress)
        return lease_session.run

    async def release_completed_pipeline(self, run: PipelineRun) -> None:
        """Apply terminal journal retention after persisted finalization.

        Args:
            run: Run whose journal may be released.
        """
        if self._journal is not None:
            await self._journal.release(run)

    async def _run_leased(
        self,
        lease_session: RunLeaseSession,
        sources: list[SourceSpec],
        *,
        on_progress: ProgressCallback | None,
    ) -> None:
        async def work() -> None:
            await self._run_leased_work(lease_session, sources, on_progress=on_progress)

        if self._journal is None:
            await work()
        else:
            await self._journal.run(
                lease_session.run,
                work,
                generation=lease_session.generation,
            )
        if lease_session.run.errors:
            raise RuntimeError("Scan has failed source work; inspect source progress")

    async def _run_leased_work(
        self,
        lease_session: RunLeaseSession,
        sources: list[SourceSpec],
        *,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Perform scan work under a heartbeat session owned by the caller."""
        run = lease_session.run
        bind_run_id(run.run_id)
        self._tracer = get_tracer("jobfeed.scan")
        self._perf_store = get_perf_store(self.store)
        self._on_progress = on_progress
        for name, _, _ in sources:
            self._publish_scan_progress(run, source=name, phase="queued")
        lease_session.ensure_active()
        tasks = [
            asyncio.create_task(
                self._scan_one_source(lease_session, run, name, source, config)
            )
            for name, source, config in sources
        ]
        work = asyncio.gather(*tasks)
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=5)
                if not work.done():
                    await self._run_orchestrator.checkpoint(lease_session)
            await work
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, *tasks, return_exceptions=True)

    async def _scan_one_source(
        self,
        lease_session: RunLeaseSession,
        run: PipelineRun,
        name: str,
        source: SourcePort,
        config: dict[str, object],
    ) -> None:
        lease_session.ensure_active()
        self._publish_scan_progress(run, source=name, phase="fetching")
        async with StepTimer(
            self._perf_store,
            run.run_id,
            "source_fetch",
            name,
            self._tracer,
        ):
            if isinstance(source, SessionSource):
                await self._scan_session_source(
                    lease_session, run, name, source, config
                )
            else:
                await self._scan_simple_source(lease_session, run, name, source, config)
        if self._on_progress is not None:
            self._on_progress(run)
        await self._run_orchestrator.checkpoint(lease_session)

    async def _scan_simple_source(
        self,
        lease_session: RunLeaseSession,
        run: PipelineRun,
        name: str,
        source: SimpleSource,
        config: dict[str, object],
    ) -> None:
        partial_failure = False
        warning = False
        detail = None
        try:
            lease_session.ensure_active()
            jobs = await self._fetch_source_jobs(source, config, run, name)
        except RunLeaseLostError:
            raise
        except PartialSourceFetchError as exc:
            partial_failure = True
            warning, detail = exc.warning, str(exc)
            if not warning:
                self.error_handler.handle_source_fetch_error(run, name, exc)
            jobs = exc.postings
        except Exception as exc:
            self.error_handler.handle_source_fetch_error(run, name, exc)
            self._publish_scan_progress(run, source=name, phase="failed")
            run.scan_progress[name]["message"] = str(exc)
            return
        _record_fetched_stats(run, name, len(jobs))
        fetched_progress = run.scan_progress.get(name, {})
        fetch_total = (
            fetched_progress.get("total")
            if fetched_progress.get("phase") == "fetching"
            else None
        )
        stats = getattr(source, "stats", None)
        if isinstance(stats, dict):
            run.scan_stats.setdefault(name, {}).update(stats)
        await self._run_orchestrator.checkpoint(lease_session)
        self._publish_scan_progress(
            run,
            source=name,
            phase="saving",
            total=len(jobs),
            processed=0,
        )
        await self._record_jobs(
            lease_session, run, name, jobs, completed=not partial_failure
        )
        if partial_failure:
            self._publish_scan_progress(
                run,
                source=name,
                phase="completed_with_warnings" if warning else "failed",
                total=fetch_total if isinstance(fetch_total, int) else None,
                processed=len(jobs),
            )
            run.scan_progress[name]["message"] = detail

    def _publish_fetch_progress(
        self,
        run: PipelineRun,
        source: str,
        progress: SourceFetchProgress,
    ) -> None:
        self._publish_scan_progress(
            run,
            source=source,
            phase=progress.phase,
            total=progress.total,
            processed=progress.processed,
            current_job_id=progress.current_job_id,
        )

    async def _scan_session_source(
        self,
        lease_session: RunLeaseSession,
        run: PipelineRun,
        name: str,
        source: SessionSource,
        config: dict[str, object],
    ) -> None:
        try:
            jobs = await self._run_session(lease_session, name, source, config)
        except RunLeaseLostError:
            raise
        except SourceBusyError as exc:
            # Contention (e.g. another LinkedIn session holds the enrich lock) is
            # a benign skip, not a fetch failure: do not count it as an error.
            self.logger.info("scan_source_busy", source=name, reason=str(exc))
            return
        except Exception as exc:
            self.error_handler.handle_source_fetch_error(run, name, exc)
            self._publish_scan_progress(run, source=name, phase="failed")
            run.scan_progress[name]["message"] = str(exc)
            return
        _record_fetched_stats(run, name, len(jobs))
        await self._run_orchestrator.checkpoint(lease_session)
        self._publish_scan_progress(
            run,
            source=name,
            phase="saving",
            total=len(jobs),
            processed=0,
        )
        await self._record_jobs(lease_session, run, name, jobs)

    async def _run_session(
        self,
        lease_session: RunLeaseSession,
        name: str,
        source: SessionSource,
        config: dict[str, object],
    ) -> list[JobPosting]:
        # ONE locked session spans discover + enrich, so the source's exclusive
        # resource (lock + browser) is held across both phases.
        lease_session.ensure_active()
        async with source.session() as session:
            lease_session.ensure_active()
            discovered = await session.discover(config)
            if discovered.needs_reauth:
                raise RuntimeError(discovered.error or "source requires reauth")
            return await self._enrich_postings(
                lease_session, name, session, discovered.postings
            )

    async def _enrich_postings(
        self,
        lease_session: RunLeaseSession,
        name: str,
        session: ScanSession,
        postings: list[JobPosting],
    ) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for posting in postings:
            lease_session.ensure_active()
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
        lease_session: RunLeaseSession,
        run: PipelineRun,
        name: str,
        jobs: list[JobPosting],
        *,
        completed: bool = True,
    ) -> None:
        before_inserted = run.jobs_inserted
        before_updated = run.jobs_updated
        await self._save_jobs(lease_session, run, name, jobs)
        inserted = run.jobs_inserted - before_inserted
        updated = run.jobs_updated - before_updated
        self.logger.info(
            "scan_source_saved",
            source=name,
            jobs_discovered=len(jobs),
            jobs_inserted=inserted,
            jobs_updated=updated,
        )
        if completed:
            self._publish_scan_progress(
                run,
                source=name,
                phase="completed",
                total=len(jobs),
                processed=len(jobs),
            )

    async def _save_jobs(
        self,
        lease_session: RunLeaseSession,
        run: PipelineRun,
        source: str,
        jobs: list[JobPosting],
    ) -> None:
        pipeline = current_pipeline.get()
        if pipeline is not None:
            await self._save_queued_jobs(lease_session, run, source, jobs, pipeline)
            return
        for processed, job in enumerate(jobs, start=1):
            lease_session.ensure_active()
            result = await self.store.save_job(job)
            run.jobs_discovered += 1
            run.jobs_inserted += int(result.inserted)
            run.jobs_updated += int(result.updated)
            _record_scan_stats(run, source, job, result)
            if result.inserted:
                run.scan_inserted_job_ids.append(result.job_id)
            if processed % _SAVE_PROGRESS_INTERVAL == 0:
                self._publish_scan_progress(
                    run,
                    source=source,
                    phase="saving",
                    total=len(jobs),
                    processed=processed,
                )
                await self._run_orchestrator.checkpoint(lease_session)

    async def _fetch_source_jobs(
        self,
        source: SimpleSource,
        config: dict[str, object],
        run: PipelineRun,
        name: str,
    ) -> list[JobPosting]:
        async def fetch(saved_config: dict[str, object]) -> dict[str, Any]:
            error = None
            warning = False
            try:
                if isinstance(source, ProgressiveSimpleSource):
                    jobs = await source.fetch_jobs_with_progress(
                        saved_config,
                        lambda p: self._publish_fetch_progress(run, name, p),
                    )
                else:
                    jobs = await source.fetch_jobs(saved_config)
            except PartialSourceFetchError as exc:
                jobs, error = exc.postings, str(exc)
                warning = exc.warning
            if pipeline:
                cached = POSTINGS.validate_python(
                    await pipeline.load_partial(f"source:{name}")
                )
                combined = {
                    (job.platform, job.canonical_id): job for job in cached + jobs
                }
                jobs = list(combined.values())
            return {
                "jobs": POSTINGS.dump_python(jobs, mode="json"),
                "error": error,
                "warning": warning,
                "stats": getattr(source, "stats", {}),
            }

        pipeline = current_pipeline.get()
        result = (
            await pipeline.step(f"source:{name}", config, fetch, retry_errors=True)
            if pipeline
            else await fetch(config)
        )
        if hasattr(source, "stats"):
            source.stats = result["stats"]
        if pipeline:
            # A recovered source can return a different batch at the same offset.
            # Its write receipts must not reuse the earlier partial result.
            self._source_write_generations[name] = result.get("generation")
        jobs = POSTINGS.validate_python(result["jobs"])
        if result["error"]:
            raise PartialSourceFetchError(
                result["error"], jobs, warning=result.get("warning", False)
            )
        return jobs

    async def _save_queued_jobs(
        self,
        session: RunLeaseSession,
        run: PipelineRun,
        source: str,
        jobs: list[JobPosting],
        pipeline: PipelineStep,
    ) -> None:
        for offset in range(0, len(jobs), 100):
            batch = jobs[offset : offset + 100]
            name = f"write:{source}:{offset}"
            generation = self._source_write_generations.get(source)
            if generation is not None:
                name += f":attempt:{generation}"

            async def write(payload: Any, name: str = name) -> list[dict[str, Any]]:
                session.ensure_active()
                async with pipeline.writer_lock:
                    saved = await cast(PipelineStore, self.store).save_job_batch(
                        POSTINGS.validate_python(payload),
                        receipt_key=f"redis-receipt:{pipeline.root}:{name}",
                        run_id=run.run_id,
                        owner_id=session.owner_id,
                        generation=session.generation,
                    )
                return [asdict(result) for result in saved]

            outcomes = await pipeline.step(
                name, POSTINGS.dump_python(batch, mode="json"), write
            )
            _record_batch_outcomes(run, source, batch, outcomes)
            self._publish_scan_progress(
                run,
                source=source,
                phase="saving",
                total=len(jobs),
                processed=offset + len(batch),
            )
            await self._run_orchestrator.checkpoint(session)

    def _publish_scan_progress(  # noqa: PLR0913 - one source progress snapshot
        self,
        run: PipelineRun,
        *,
        source: str,
        phase: str,
        total: int | None = None,
        processed: int = 0,
        current_job_id: str | None = None,
    ) -> None:
        """Publish the active source and its bounded work when available."""
        run.scan_source = source
        run.scan_phase = phase
        run.scan_total = total
        run.scan_processed = processed
        run.scan_current_job_id = current_job_id
        run.progress_updated_at = datetime.now(UTC)
        run.scan_progress[source] = {
            "phase": phase,
            "total": total,
            "processed": processed,
            "current_job_id": current_job_id,
            "updated_at": run.progress_updated_at.isoformat(),
        }
        if self._on_progress is not None:
            self._on_progress(run)


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
        apply_url=result.apply_url or posting.apply_url,
    )


def _record_scan_stats(
    run: PipelineRun,
    source: str,
    job: JobPosting,
    result: SaveJobResult,
) -> None:
    """Capture incoming scan quality before a later upsert can replace the job."""
    stats = run.scan_stats.setdefault(
        source,
        {
            "fetched": 0,
            "discovered": 0,
            "inserted": 0,
            "updated": 0,
            "has_jd": 0,
        },
    )
    stats["discovered"] += 1
    stats["inserted"] += int(result.inserted)
    stats["updated"] += int(result.updated)
    if job.jd_text is not None and job.jd_text.strip():
        stats["has_jd"] += 1
    quality = job.jd_quality or assess_quality(job.jd_text)
    stats[quality.value] = stats.get(quality.value, 0) + 1


def _record_fetched_stats(run: PipelineRun, source: str, fetched: int) -> None:
    """Persist the source result size before individual job writes begin."""
    stats = run.scan_stats.setdefault(
        source,
        {
            "fetched": 0,
            "discovered": 0,
            "inserted": 0,
            "updated": 0,
            "has_jd": 0,
        },
    )
    stats["fetched"] = fetched


def run_source_name(sources: list[SourceSpec]) -> str:
    """Derive a run's source label from its source specs.

    Args:
        sources: Source specs the scan will run.

    Returns:
        The sole source's name for single-source scans, else "scan".
    """
    if len(sources) == SINGLE_SOURCE_COUNT:
        return sources[0][0]
    return "scan"


__all__ = ["ScanService", "SourceSpec", "run_source_name"]


def _record_batch_outcomes(
    run: PipelineRun,
    source: str,
    batch: list[JobPosting],
    outcomes: list[dict[str, Any]],
) -> None:
    """Fold persisted batch receipts into counters without repeating writes."""
    for job, item in zip(batch, outcomes, strict=True):
        result = SaveJobResult(**item)
        run.jobs_discovered += 1
        run.jobs_inserted += int(result.inserted)
        run.jobs_updated += int(result.updated)
        _record_scan_stats(run, source, job, result)
        if result.inserted:
            run.scan_inserted_job_ids.append(result.job_id)
