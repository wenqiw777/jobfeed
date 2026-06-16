"""Async task lifecycle manager for web-triggered pipeline runs.

Provides concurrency locks so only one scan and one evaluate can run at a
time, progress broadcast via per-subscriber asyncio.Queue, and stale-run
recovery on startup.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jobfeed.domain.errors import RunConflictError
from jobfeed.domain.models import PipelineRun
from jobfeed.services.runs import start_pipeline_run

if TYPE_CHECKING:
    from jobfeed.observability import JobfeedLogger
    from jobfeed.ports.store import JobStore
    from jobfeed.services.evaluate import EvaluateService
    from jobfeed.services.scan import ScanService, SourceSpec

_SENTINEL = object()


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
    ) -> None:
        """Create a RunManager with injected factories.

        Args:
            store: Persistence port for run status transitions.
            logger: Structured logger for lifecycle events.
            scan_service_factory: Zero-arg callable returning a ScanService.
            evaluate_service_factory: Callable accepting keyword args and
                returning an EvaluateService.
        """
        self._store = store
        self._logger = logger
        self._scan_factory = scan_service_factory
        self._eval_factory = evaluate_service_factory
        self._scan_lock = asyncio.Lock()
        self._eval_lock = asyncio.Lock()
        self._active: dict[str, ActiveRun] = {}
        self._subscribers: dict[str, list[asyncio.Queue[PipelineRun | object]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def trigger_scan(self, sources: list[SourceSpec]) -> str:
        """Start a scan run if no scan is already active.

        The lock is acquired eagerly before the background task is created
        so concurrent callers see it immediately.

        Args:
            sources: Source specs to pass to ScanService.run().

        Returns:
            The run_id of the newly started scan.

        Raises:
            RunConflictError: If a scan is already in progress.
        """
        self._require_unlocked(self._scan_lock, "scan")
        await self._scan_lock.acquire()
        run = start_pipeline_run("scan")
        self._register(run, "scan")
        service = self._scan_factory()

        async def _call() -> None:
            await service.run(sources, on_progress=self._make_progress(run.run_id))

        self._tasks[run.run_id] = asyncio.create_task(
            self._execute_run(self._scan_lock, run, _call)
        )
        return run.run_id

    async def trigger_evaluate(self, **kwargs: Any) -> str:
        """Start an evaluate run if no evaluate is already active.

        Args:
            kwargs: Forwarded to evaluate_service_factory and service.run().

        Returns:
            The run_id of the newly started evaluation.

        Raises:
            RunConflictError: If an evaluate is already in progress.
        """
        self._require_unlocked(self._eval_lock, "evaluation")
        await self._eval_lock.acquire()
        run = start_pipeline_run("evaluate")
        self._register(run, "evaluate")
        service = self._eval_factory(**kwargs)

        async def _call() -> None:
            await service.run(on_progress=self._make_progress(run.run_id), **kwargs)

        self._tasks[run.run_id] = asyncio.create_task(
            self._execute_run(self._eval_lock, run, _call)
        )
        return run.run_id

    async def _execute_run(
        self,
        lock: asyncio.Lock,
        run: PipelineRun,
        service_call: Callable[[], object],
    ) -> None:
        """Run a service call, then release the lock and finalize the run.

        The lock is already held when this coroutine starts. It is released
        in the finally block regardless of outcome.

        Args:
            lock: The already-acquired asyncio.Lock for this run type.
            run: The PipelineRun tracking counters.
            service_call: The async callable that does the actual work.
        """
        try:
            await service_call()  # type: ignore[misc]
            await self._finish_run(run, "succeeded")
        except Exception as exc:
            self._logger.error("run_failed", run_id=run.run_id, error=str(exc))
            await self._finish_run(run, "failed")
        finally:
            lock.release()

    def _register(self, run: PipelineRun, source: str) -> None:
        """Track a run as active.

        Args:
            run: The pipeline run to register.
            source: Label for the run type (e.g. "scan", "evaluate").
        """
        self._active[run.run_id] = ActiveRun(
            run_id=run.run_id,
            source=source,
            started_at=run.started_at,
            run=run,
        )

    @staticmethod
    def _require_unlocked(lock: asyncio.Lock, label: str) -> None:
        """Raise RunConflictError if the lock is already held.

        Args:
            lock: The asyncio.Lock to check.
            label: Human label for error messages.

        Raises:
            RunConflictError: If the lock is currently acquired.
        """
        if lock.locked():
            raise RunConflictError(f"A {label} is already running")

    async def _finish_run(self, run: PipelineRun, status: str) -> None:
        """Finalize a run: update status, broadcast, clean up.

        Args:
            run: The PipelineRun to finalize.
            status: Terminal status ("succeeded" or "failed").
        """
        now = datetime.now(UTC)
        run.status = status
        run.finished_at = now
        await self._store.update_pipeline_run_status(run.run_id, status, now)
        self._broadcast(run.run_id, run)
        self._close_subscribers(run.run_id)
        self._active.pop(run.run_id, None)
        self._tasks.pop(run.run_id, None)

    def get_active_runs(self) -> list[ActiveRun]:
        """Return all currently active runs.

        Returns:
            List of active runs, ordered by start time.
        """
        return sorted(self._active.values(), key=lambda r: r.started_at)

    async def subscribe(self, run_id: str) -> AsyncIterator[PipelineRun]:
        """Subscribe to progress events for a run.

        Yields PipelineRun snapshots as the run progresses. The iterator
        terminates when the run finishes (a sentinel is pushed).

        Args:
            run_id: Identity of the run to subscribe to.

        Returns:
            Async iterator of PipelineRun progress snapshots.
        """
        queue: asyncio.Queue[PipelineRun | object] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item  # type: ignore[misc]
        finally:
            subs = self._subscribers.get(run_id, [])
            if queue in subs:
                subs.remove(queue)

    def _make_progress(self, run_id: str) -> Callable[[PipelineRun], None]:
        """Create a progress callback that broadcasts to subscribers.

        Args:
            run_id: Run to broadcast progress for.

        Returns:
            A callback suitable for ScanService/EvaluateService on_progress.
        """

        def _on_progress(run: PipelineRun) -> None:
            self._broadcast(run_id, run)

        return _on_progress

    def _broadcast(self, run_id: str, run: PipelineRun) -> None:
        """Push a progress snapshot to all subscribers for a run.

        Args:
            run_id: Run to broadcast for.
            run: Current run state to push.
        """
        for queue in self._subscribers.get(run_id, []):
            queue.put_nowait(run)

    def _close_subscribers(self, run_id: str) -> None:
        """Signal all subscribers that a run has finished.

        Args:
            run_id: Run whose subscribers to close.
        """
        for queue in self._subscribers.pop(run_id, []):
            queue.put_nowait(_SENTINEL)

    async def recover_stale_runs(self) -> int:
        """Transition any runs stuck in 'running' status to 'failed'.

        Called at startup to recover from unclean shutdowns. Returns the
        count of runs recovered.

        Returns:
            Number of stale runs transitioned to 'failed'.
        """
        now = datetime.now(UTC)
        store = self._store
        if not hasattr(store, "list_pipeline_runs"):
            return 0
        runs, _ = await store.list_pipeline_runs(limit=100)
        recovered = 0
        for run in runs:
            if run.status == "running":
                await self._store.update_pipeline_run_status(run.run_id, "failed", now)
                self._logger.info("recovered_stale_run", run_id=run.run_id)
                recovered += 1
        return recovered


__all__ = ["ActiveRun", "RunConflictError", "RunManager"]
