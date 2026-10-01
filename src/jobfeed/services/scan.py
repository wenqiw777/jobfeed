"""Scan service that persists jobs from configured source ports."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any, cast

from jobfeed.domain.errors import RunLeaseLostError, SourceBusyError
from jobfeed.domain.intermediary import blocked_publisher_company, intermediary_posting
from jobfeed.domain.models import JobPosting, PipelineRun, SaveJobResult
from jobfeed.domain.quality import assess_quality
from jobfeed.observability import JobfeedLogger, bind_run_id, get_tracer
from jobfeed.ports.application_resolution import (
    ApplicationIdentityStore,
    PostingBatchCallback,
    StreamingApplicationSource,
)
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
from jobfeed.services.application_resolution import (
    ApplicationResolutionQueue,
    RouteResolver,
)
from jobfeed.services.error_handler import ServiceErrorHandler
from jobfeed.services.intermediary_resolution import IntermediaryResolver
from jobfeed.services.pipeline_context import POSTINGS, current_pipeline
from jobfeed.services.run_orchestration import RunLeaseOrchestrator, RunLeaseSession
from jobfeed.services.scan_streaming import ScanBatchWriter

ProgressCallback = Callable[[PipelineRun], None]

SourcePort = SimpleSource | SessionSource
SourceSpec = tuple[str, SourcePort, dict[str, object]]
SINGLE_SOURCE_COUNT = 1
_SAVE_PROGRESS_INTERVAL = 100


class ScanService:
    """Application service for source fetch and job persistence."""

    def __init__(  # noqa: PLR0913 - independently injected scan services
        self,
        store: JobStore,
        logger: JobfeedLogger,
        run_orchestrator: RunLeaseOrchestrator | None = None,
        *,
        journal: ScanJournal | None = None,
        intermediary: IntermediaryResolver | None = None,
        application_resolver: RouteResolver | None = None,
    ) -> None:
        """Create a scan service with injected ports.

        Args:
            store: Persistence port used to save jobs and pipeline metrics.
            logger: Structured logger for scan events.
        """
        self._intermediary = intermediary
        self._application_resolver = application_resolver
        self._application_queue: ApplicationResolutionQueue | None = None
        self._writer_lock = asyncio.Lock()
        self._intermediary_jobs: list[JobPosting] = []
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
        """Own asynchronous route work alongside source collection and writes."""
        pipeline = current_pipeline.get()
        self._writer_lock = pipeline.writer_lock if pipeline else asyncio.Lock()
        if self._application_resolver is None:
            await self._run_leased_sources(
                lease_session, sources, on_progress=on_progress
            )
            return

        def progress(done: int, total: int, status: str) -> None:
            self._publish_scan_progress(
                lease_session.run,
                source="application-resolution",
                phase="resolving",
                processed=done,
                total=total,
            )
            lease_session.run.scan_progress["application-resolution"]["outcome"] = (
                status
            )

        queue = ApplicationResolutionQueue(
            cast(ApplicationIdentityStore, self.store),
            self._application_resolver,
            run_id=lease_session.run.run_id,
            ensure_active=lease_session.ensure_active,
            writer_lock=self._writer_lock,
            on_progress=progress,
            lease_fence=(
                lease_session.run.run_id,
                lease_session.owner_id,
                lease_session.generation,
            )
            if hasattr(self.store, "save_job_batch")
            else None,
        )
        async with queue:
            self._application_queue = queue
            try:
                await self._run_leased_sources(
                    lease_session, sources, on_progress=on_progress
                )
            finally:
                self._application_queue = None

    async def _run_leased_sources(
        self,
        lease_session: RunLeaseSession,
        sources: list[SourceSpec],
        *,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Perform source work under a heartbeat session owned by the caller."""
        run = lease_session.run
        self._intermediary_jobs = []
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
            await self._drain_application_resolution(lease_session, tasks)
            if self._intermediary is not None and self._intermediary_jobs:

                def progress(done: int, total: int) -> None:
                    lease_session.ensure_active()
                    self._publish_scan_progress(
                        run,
                        source="intermediary",
                        phase="enriching",
                        total=total,
                        processed=done,
                    )
                    if self._on_progress:
                        self._on_progress(run)

                resolution = asyncio.create_task(
                    self._intermediary.resolve(
                        self._intermediary_jobs,
                        run_id=run.run_id,
                        ensure_active=lease_session.ensure_active,
                        on_progress=progress,
                    )
                )
                tasks.append(resolution)
                while not resolution.done():
                    await asyncio.wait({resolution}, timeout=5)
                    await self._run_orchestrator.checkpoint(lease_session)
                await resolution
                self._publish_scan_progress(
                    run,
                    source="intermediary",
                    phase="completed",
                    total=len(self._intermediary_jobs),
                    processed=len(self._intermediary_jobs),
                )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, *tasks, return_exceptions=True)

    async def _drain_application_resolution(
        self, session: RunLeaseSession, tasks: list[asyncio.Task[None]]
    ) -> None:
        if self._application_queue is None:
            return
        resolution = asyncio.create_task(self._application_queue.drain())
        tasks.append(resolution)
        while not resolution.done():
            await asyncio.wait({resolution}, timeout=5)
            await self._run_orchestrator.checkpoint(session)
        await resolution
        self._publish_scan_progress(
            session.run,
            source="application-resolution",
            phase="completed",
            processed=self._application_queue.completed,
            total=self._application_queue.total,
        )

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
        streamed: set[tuple[str, str]] = set()

        self._source_write_generations[name] = str(lease_session.generation)

        async def save_batch(batch: list[JobPosting], generation: str | None) -> None:
            await self._save_jobs(
                lease_session,
                run,
                name,
                batch,
                streaming=True,
                stream_generation=generation,
            )
            self._intermediary_jobs.extend(j for j in batch if intermediary_posting(j))

        writer = ScanBatchWriter(save_batch)

        async def submit_batch(batch: list[JobPosting]) -> None:
            await self._accept_stream_batch(
                name, writer, batch, str(lease_session.generation)
            )

        try:
            lease_session.ensure_active()
            async with writer:
                await self._restore_stream_batches(name, writer)
                jobs = await self._fetch_source_jobs(
                    source,
                    config,
                    run,
                    name,
                    on_batch=submit_batch
                    if isinstance(source, StreamingApplicationSource)
                    else None,
                )
            streamed = writer.seen
        except RunLeaseLostError:
            raise
        except PartialSourceFetchError as exc:
            partial_failure = True
            warning, detail = exc.warning, str(exc)
            if not warning:
                self.error_handler.handle_source_fetch_error(run, name, exc)
            jobs = exc.postings
            streamed = writer.seen
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
            lease_session,
            run,
            name,
            [j for j in jobs if (j.platform, j.canonical_id) not in streamed],
            completed=False,
        )
        if not partial_failure:
            self._publish_scan_progress(
                run,
                source=name,
                phase="completed",
                processed=len(jobs),
                total=len(jobs),
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

    @staticmethod
    async def _accept_stream_batch(
        source: str, writer: ScanBatchWriter, batch: list[JobPosting], generation: str
    ) -> None:
        """Persist immutable source payloads before acknowledging their queue slots."""
        fresh = writer.unseen(batch)
        if not fresh:
            return
        pipeline = current_pipeline.get()
        if pipeline is not None:
            await pipeline.save_partial(
                f"source-stream:{source}",
                [
                    {
                        "generation": generation,
                        "jobs": POSTINGS.dump_python(fresh, mode="json"),
                    }
                ],
            )
        await writer.submit(fresh, generation=generation if pipeline else None)

    @staticmethod
    async def _restore_stream_batches(source: str, writer: ScanBatchWriter) -> None:
        """Replay original serialized batches before a browser remaps their dates."""
        pipeline = current_pipeline.get()
        if pipeline is None:
            return
        for manifest in await pipeline.load_partial(f"source-stream:{source}"):
            generation = manifest.get("generation")
            if not isinstance(generation, str):
                raise ValueError("stream manifest lacks its original generation")
            await writer.submit(
                POSTINGS.validate_python(manifest["jobs"]), generation=generation
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
        self._intermediary_jobs.extend(j for j in jobs if intermediary_posting(j))
        self._publish_scan_progress(
            run, source=name, phase="completed", processed=len(jobs), total=len(jobs)
        )

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
            if blocked_publisher_company(posting.company):
                continue
            result = await session.enrich(posting)
            if result.error is not None:
                self.logger.error(
                    "scan_posting_enrich_failed",
                    source=name,
                    canonical_id=posting.canonical_id,
                    error=result.error,
                )
            jobs.append(_merge_enrichment(posting, result))
            await self._save_jobs(
                lease_session, lease_session.run, name, [jobs[-1]], streaming=True
            )
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
        allowed = [job for job in jobs if not blocked_publisher_company(job.company)]
        if len(allowed) != len(jobs):
            self.logger.info(
                "scan_publishers_blocked", source=name, count=len(jobs) - len(allowed)
            )
        jobs = allowed
        self._intermediary_jobs.extend(job for job in jobs if intermediary_posting(job))
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

    async def _save_jobs(  # noqa: PLR0913 - explicit immutable batch generation
        self,
        lease_session: RunLeaseSession,
        run: PipelineRun,
        source: str,
        jobs: list[JobPosting],
        *,
        streaming: bool = False,
        stream_generation: str | None = None,
    ) -> None:
        pipeline = current_pipeline.get()
        if pipeline is not None:
            await self._save_queued_jobs(
                lease_session,
                run,
                source,
                jobs,
                pipeline,
                streaming=streaming,
                stream_generation=stream_generation,
            )
            return
        for processed, job in enumerate(jobs, start=1):
            lease_session.ensure_active()
            async with self._writer_lock:
                lease_session.ensure_active()
                result = await self.store.save_job(job)
            run.jobs_discovered += 1
            run.jobs_inserted += int(result.inserted)
            run.jobs_updated += int(result.updated)
            _record_scan_stats(run, source, job, result)
            if result.inserted:
                run.scan_inserted_job_ids.append(result.job_id)
            if self._application_queue is not None:
                await self._application_queue.submit_id(result.job_id)
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
        *,
        on_batch: PostingBatchCallback | None = None,
    ) -> list[JobPosting]:
        async def fetch(saved_config: dict[str, object]) -> dict[str, Any]:
            error = None
            warning = False
            try:
                if on_batch is not None and isinstance(
                    source, StreamingApplicationSource
                ):
                    jobs = await source.fetch_jobs_streaming(
                        saved_config,
                        lambda p: self._publish_fetch_progress(run, name, p),
                        on_batch,
                    )
                elif isinstance(source, ProgressiveSimpleSource):
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

    async def _save_queued_jobs(  # noqa: PLR0913 - fenced batch write context
        self,
        session: RunLeaseSession,
        run: PipelineRun,
        source: str,
        jobs: list[JobPosting],
        pipeline: PipelineStep,
        *,
        streaming: bool = False,
        stream_generation: str | None = None,
    ) -> None:
        for offset in range(0, len(jobs), 100):
            batch = jobs[offset : offset + 100]
            name = f"write:{source}:{offset}"
            if streaming:
                identity = [
                    (
                        j.platform,
                        j.canonical_id,
                        j.apply_url,
                        j.enriched_at.isoformat() if j.enriched_at else None,
                    )
                    for j in batch
                ]
                name = f"write:{source}:stream:" + json.dumps(
                    identity, separators=(",", ":")
                )
            generation = stream_generation or self._source_write_generations.get(source)
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
            await self._enqueue_application_outcomes(outcomes)
            self._publish_scan_progress(
                run,
                source=source,
                phase="saving",
                total=len(jobs),
                processed=offset + len(batch),
            )
            await self._run_orchestrator.checkpoint(session)

    async def _enqueue_application_outcomes(
        self, outcomes: list[dict[str, Any]]
    ) -> None:
        if self._application_queue is None:
            return
        for result in outcomes:
            await self._application_queue.submit_id(str(result["job_id"]))

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
        identity_evidence_url=result.identity_evidence_url
        or posting.identity_evidence_url,
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
