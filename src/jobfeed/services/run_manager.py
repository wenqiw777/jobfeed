"""Async task lifecycle for web-triggered pipeline runs.

Concurrency locks (one scan + one evaluate at a time), progress broadcast via
per-subscriber asyncio.Queue, and stale-run recovery on startup.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import PipelineRun
from jobfeed.services.runs import start_pipeline_run
from jobfeed.services.scan import SourceSpec, run_source_name

if TYPE_CHECKING:
    from jobfeed.observability import JobfeedLogger
    from jobfeed.ports.store import JobStore
    from jobfeed.services.evaluate import EvaluateService
    from jobfeed.services.scan import ScanService

RUN_DONE = object()
"""Queue sentinel signalling that a subscribed run has finished."""

SourceResolver = Callable[[str, contextlib.AsyncExitStack], Awaitable[list[SourceSpec]]]
"""Composition-injected resolver: source token -> SourceSpec entries; raises a
domain error (SourceConfigError) so this service never imports CLI/adapters."""


@dataclass
class ActiveRun:
    """A currently executing pipeline run."""

    run_id: str
    source: str
    started_at: datetime
    run: PipelineRun


class RunManager:
    """Async task lifecycle manager for web-triggered pipeline runs.

    Uses asyncio.Lock per run type (scan / evaluate) acquired eagerly so
    a second concurrent trigger raises RunConflictError immediately.
    """

    def __init__(
        self,
        store: JobStore,
        logger: JobfeedLogger,
        scan_service_factory: Callable[[], ScanService],
        evaluate_service_factory: Callable[..., EvaluateService],
        scan_source_resolver: SourceResolver | None = None,
    ) -> None:
        """Create a RunManager with injected factories and source resolver."""
        self._store = store
        self._logger = logger
        self._scan_factory = scan_service_factory
        self._eval_factory = evaluate_service_factory
        self._source_resolver = scan_source_resolver
        self._scan_lock = asyncio.Lock()
        self._eval_lock = asyncio.Lock()
        self._active: dict[str, ActiveRun] = {}
        self._subscribers: dict[str, list[asyncio.Queue[PipelineRun | object]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def trigger_scan(self, source_name_or_specs: str | list[SourceSpec]) -> str:
        """Start a scan if none active.

        Args:
            source_name_or_specs: Source token or pre-built SourceSpec list.
                Resolved before the run is created, so a disabled source is
                an immediate error to the caller, not a vanished run.

        Returns: The new run's run_id.
        Raises: RunConflictError if a scan is already running.
        """
        self._require_unlocked(self._scan_lock, "scan")
        await self._scan_lock.acquire()
        stack = contextlib.AsyncExitStack()
        run: PipelineRun | None = None
        try:
            specs = await self._resolve_sources(source_name_or_specs, stack)
            source = self._source_label(source_name_or_specs, specs)
            run = start_pipeline_run(source)
            current_run = run
            self._register(run, source)
            service = self._scan_factory()
            cb = self._make_progress(run.run_id)

            async def _call() -> None:
                await service.run(specs, on_progress=cb, run=current_run)

            self._tasks[run.run_id] = asyncio.create_task(
                self._execute_run(self._scan_lock, run, _call, stack)
            )
            return run.run_id
        except Exception:
            if run is not None:
                self._active.pop(run.run_id, None)
            await stack.aclose()
            self._scan_lock.release()
            raise

    async def trigger_evaluate(self, **kwargs: Any) -> str:
        """Start an evaluate if none active.

        Args:
            kwargs: Forwarded to evaluate_service_factory and service.run().
                The service is built before the run is registered, so a
                factory failure leaves no phantom active run.

        Returns: The new run's run_id.
        Raises: RunConflictError if an evaluate is already running.
        """
        self._require_unlocked(self._eval_lock, "evaluation")
        await self._eval_lock.acquire()
        run: PipelineRun | None = None
        try:
            service = self._eval_factory(**kwargs)
            run = start_pipeline_run("evaluate")
            current_run = run
            self._register(run, "evaluate")
            cb = self._make_progress(run.run_id)

            async def _call() -> None:
                await service.run(on_progress=cb, run=current_run, **kwargs)

            self._tasks[run.run_id] = asyncio.create_task(
                self._execute_run(self._eval_lock, run, _call)
            )
            return run.run_id
        except Exception:
            if run is not None:
                self._active.pop(run.run_id, None)
            self._eval_lock.release()
            raise

    async def _execute_run(
        self,
        lock: asyncio.Lock,
        run: PipelineRun,
        service_call: Callable[[], Awaitable[None]],
        stack: contextlib.AsyncExitStack | None = None,
    ) -> None:
        """Run service_call, finalize run, close resources, release lock."""
        try:
            await service_call()
            await self._finish_run(run, "succeeded")
        except Exception as exc:
            self._logger.error("run_failed", run_id=run.run_id, error=str(exc))
            await self._finish_run(run, "failed")
        finally:
            if stack is not None:
                await stack.aclose()
            lock.release()

    async def _resolve_sources(
        self,
        name_or_specs: str | list[SourceSpec],
        stack: contextlib.AsyncExitStack,
    ) -> list[SourceSpec]:
        """Build SourceSpec list from a name string or pass through a list."""
        if isinstance(name_or_specs, list):
            return name_or_specs
        if self._source_resolver is None:
            msg = "RunManager has no scan source resolver configured"
            raise RuntimeError(msg)
        return await self._source_resolver(name_or_specs, stack)

    @staticmethod
    def _source_label(
        name_or_specs: str | list[SourceSpec], specs: list[SourceSpec]
    ) -> str:
        """Label the run with the requested token or the specs' source name."""
        if isinstance(name_or_specs, str):
            return name_or_specs
        return run_source_name(specs)

    def _register(self, run: PipelineRun, source: str) -> None:
        """Track a run as active."""
        self._active[run.run_id] = ActiveRun(
            run_id=run.run_id,
            source=source,
            started_at=run.started_at,
            run=run,
        )

    @staticmethod
    def _require_unlocked(lock: asyncio.Lock, label: str) -> None:
        """Raise RunConflictError if the lock is already held."""
        if lock.locked():
            raise RunConflictError(f"A {label} is already running")

    async def _finish_run(self, run: PipelineRun, status: str) -> None:
        """Persist terminal status (inserting a row the service never recorded,
        keeping a status it already finalized), broadcast, drop tracking."""
        if run.status == "running":
            run.status = status
            run.finished_at = datetime.now(UTC)
        if await self._store.get_pipeline_run(run.run_id) is None:
            await self._store.record_pipeline_run(run)
        await self._store.update_pipeline_run_status(run)
        self._broadcast(run.run_id, run)
        self._close_subscribers(run.run_id)
        self._active.pop(run.run_id, None)
        self._tasks.pop(run.run_id, None)

    def get_active_runs(self) -> list[ActiveRun]:
        """Return all currently active runs.

        Returns: Active runs, ordered by start time.
        """
        return sorted(self._active.values(), key=lambda r: r.started_at)

    def subscribe(self, run_id: str) -> asyncio.Queue[PipelineRun | object]:
        """Register a progress queue for a run.

        Args:
            run_id: Run to stream progress for.

        Returns: Queue of PipelineRun snapshots followed by RUN_DONE. A run
            no longer active gets RUN_DONE immediately, so a subscriber that
            loses the finish race terminates instead of waiting forever.
            Pass the queue to unsubscribe() when done.
        """
        queue: asyncio.Queue[PipelineRun | object] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        if run_id not in self._active:
            queue.put_nowait(RUN_DONE)
        return queue

    def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[PipelineRun | object]
    ) -> None:
        """Remove a subscriber queue registered via subscribe().

        Args:
            run_id: Run the queue was subscribed to.
            queue: The queue returned by subscribe().
        """
        subs = self._subscribers.get(run_id)
        if subs is None:
            return
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(run_id, None)

    def _make_progress(self, run_id: str) -> Callable[[PipelineRun], None]:
        """Create a progress callback that broadcasts to subscribers."""

        def _on_progress(run: PipelineRun) -> None:
            self._broadcast(run_id, run)

        return _on_progress

    def _broadcast(self, run_id: str, run: PipelineRun) -> None:
        """Push a snapshot per event: services mutate one PipelineRun in
        place, and a live reference would rewrite undrained queue items."""
        queues = self._subscribers.get(run_id, [])
        if not queues:
            return
        snapshot = replace(run, dry_run_preview=list(run.dry_run_preview))
        for queue in queues:
            queue.put_nowait(snapshot)

    def _close_subscribers(self, run_id: str) -> None:
        """Signal all subscribers that a run has finished."""
        for queue in self._subscribers.pop(run_id, []):
            queue.put_nowait(RUN_DONE)

    async def recover_stale_runs(self) -> int:
        """Transition runs stuck in 'running' to 'failed' at startup.

        Returns: Count of recovered stale runs.
        """
        store = self._store
        try:
            list_fn = store.list_pipeline_runs  # type: ignore[attr-defined]
        except AttributeError:
            return 0
        runs, _ = await list_fn(limit=100)
        recovered = 0
        for run in runs:
            if run.status == "running":
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                await self._store.update_pipeline_run_status(run)
                self._logger.info("recovered_stale_run", run_id=run.run_id)
                recovered += 1
        return recovered


__all__ = ["RUN_DONE", "ActiveRun", "RunConflictError", "RunManager", "SourceResolver"]
