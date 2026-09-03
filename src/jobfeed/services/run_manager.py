"""Async task lifecycle for web-triggered pipeline runs.

Concurrency locks (one scan + one evaluate at a time), progress broadcast via
per-subscriber asyncio.Queue, and fenced persistence delegated to the shared
run-lease orchestrator.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from jobfeed.domain.errors import RunConflictError, RunLeaseLostError
from jobfeed.domain.models import PipelineRun
from jobfeed.ports.run_leases import RecoverableRunLeaseStore, RunLeaseStore
from jobfeed.services.run_orchestration import RunLeaseOrchestrator, RunLeaseSession
from jobfeed.services.run_tracking import ActiveRun, RunProgressBroker
from jobfeed.services.scan import SourceSpec, run_source_name

if TYPE_CHECKING:
    from jobfeed.observability import JobfeedLogger
    from jobfeed.ports.store import JobStore
    from jobfeed.services.evaluate import EvaluateService
    from jobfeed.services.scan import ScanService

RUN_DONE = object()
"""Queue sentinel signalling that a subscribed run has finished."""

_EVALUATION_CHECKPOINT_INTERVAL = 100

SourceResolver = Callable[[str, contextlib.AsyncExitStack], Awaitable[list[SourceSpec]]]
"""Composition-injected resolver: source token -> SourceSpec entries; raises a
domain error (SourceConfigError) so this service never imports CLI/adapters."""

PostScanHook = Callable[
    [PipelineRun, list[SourceSpec], Callable[[PipelineRun], None]], Awaitable[None]
]
"""Optional web-only work that extends a scan's live progress stream."""


class RunManager:
    """Async task lifecycle manager for web-triggered pipeline runs.

    Uses asyncio.Lock per run type (scan / evaluate) acquired eagerly so
    a second concurrent trigger raises RunConflictError immediately.
    """

    def __init__(  # noqa: PLR0913 - factories plus shared lease orchestration
        self,
        store: JobStore,
        logger: JobfeedLogger,
        scan_service_factory: Callable[[], ScanService],
        evaluate_service_factory: Callable[..., EvaluateService],
        scan_source_resolver: SourceResolver | None = None,
        run_orchestrator: RunLeaseOrchestrator | None = None,
        post_scan_hook: PostScanHook | None = None,
        auto_restart_allowed: Callable[[str], bool] | None = None,
    ) -> None:
        """Create a RunManager with injected factories and source resolver."""
        self._logger = logger
        self._store = store
        self._scan_factory = scan_service_factory
        self._eval_factory = evaluate_service_factory
        self._source_resolver = scan_source_resolver
        self._post_scan_hook = post_scan_hook
        self._auto_restart_allowed = auto_restart_allowed or (lambda _source: True)
        self._run_orchestrator = run_orchestrator or RunLeaseOrchestrator(
            cast(RunLeaseStore, store)
        )
        self._scan_lock = asyncio.Lock()
        self._eval_lock = asyncio.Lock()
        self._active: dict[str, ActiveRun] = {}
        self._progress = RunProgressBroker(RUN_DONE)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._checkpoint_tasks: dict[str, set[asyncio.Task[bool]]] = {}

    async def trigger_scan(
        self,
        source_name_or_specs: str | list[SourceSpec],
        *,
        restart_count: int = 0,
    ) -> str:
        """Start a scan if none active.

        Args:
            source_name_or_specs: Source token or pre-built SourceSpec list.
                The lease is acquired before resolver store work; resolver
                failures are returned immediately after fenced failure.

        Returns: The new run's run_id.
        Raises: RunConflictError if a scan is already running.
        """
        self._require_unlocked(self._scan_lock, "scan")
        await self._scan_lock.acquire()
        stack = contextlib.AsyncExitStack()
        session: RunLeaseSession | None = None
        try:
            service = self._scan_factory()
            source = self._source_label_before_resolution(source_name_or_specs)
            session = await self._run_orchestrator.start(
                "scan", source, restart_count=restart_count
            )
            self._register(session.run, source)
            specs = await self._resolve_sources(source_name_or_specs, stack)
            cb = self._make_progress(session.run.run_id, session)

            async def _work(active_session: RunLeaseSession) -> None:
                await service.run(
                    specs,
                    on_progress=cb,
                    lease_session=active_session,
                )
                await self._persist_scan_insertions(active_session.run)
                if self._post_scan_hook is not None:
                    await self._post_scan_hook(active_session.run, specs, cb)

            self._tasks[session.run.run_id] = asyncio.create_task(
                self._execute_run(self._scan_lock, session, _work, stack)
            )
            return session.run.run_id
        except Exception as exc:
            try:
                if session is not None:
                    self._active.pop(session.run.run_id, None)
                    await self._run_orchestrator.fail(session, exc)
            finally:
                try:
                    await stack.aclose()
                finally:
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
        session: RunLeaseSession | None = None
        try:
            scope = str(kwargs.pop("scope", "latest_scan"))
            if scope == "latest_scan":
                kwargs["job_ids"] = await self._latest_scan_inserted_job_ids()
            elif scope != "backlog":
                raise ValueError(f"unknown evaluation scope: {scope!r}")
            service = self._eval_factory(**kwargs)
            dry_run = bool(kwargs.get("dry_run", False))
            if dry_run:
                run = self._run_orchestrator.new_unpersisted_run("evaluate")
            else:
                session = await self._run_orchestrator.start("evaluate", "evaluate")
                run = session.run
            run.evaluation_scope = scope
            scoped_job_ids = kwargs.get("job_ids")
            run.evaluation_input_total = (
                len(scoped_job_ids) if isinstance(scoped_job_ids, list) else None
            )
            self._register(run, "evaluate")
            cb = self._make_progress(run.run_id, session)

            if session is None:

                async def _dry_call() -> None:
                    await service.run(on_progress=cb, run=run, **kwargs)

                task = self._execute_unpersisted_run(self._eval_lock, run, _dry_call)
            else:

                async def _work(active_session: RunLeaseSession) -> None:
                    await service.run(
                        on_progress=cb,
                        lease_session=active_session,
                        **kwargs,
                    )

                task = self._execute_run(self._eval_lock, session, _work)
            self._tasks[run.run_id] = asyncio.create_task(task)
            return run.run_id
        except Exception as exc:
            if run is not None:
                self._active.pop(run.run_id, None)
            try:
                if session is not None:
                    await self._run_orchestrator.fail(session, exc)
            finally:
                self._eval_lock.release()
            raise

    async def _persist_scan_insertions(self, run: PipelineRun) -> None:
        setter = getattr(self._store, "set_state", None)
        if setter is None:
            return
        payload = json.dumps(
            {"run_id": run.run_id, "job_ids": run.scan_inserted_job_ids},
            separators=(",", ":"),
        )
        await setter("latest_scan_inserted_job_ids", payload)

    async def _latest_scan_inserted_job_ids(self) -> list[str]:
        getter = getattr(self._store, "get_state", None)
        if getter is None:
            return []
        raw = await getter("latest_scan_inserted_job_ids")
        if raw is None:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        values = payload.get("job_ids") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return []
        return [value for value in values if isinstance(value, str)]

    async def _execute_run(
        self,
        lock: asyncio.Lock,
        session: RunLeaseSession,
        work: Callable[[RunLeaseSession], Awaitable[None]],
        stack: contextlib.AsyncExitStack | None = None,
    ) -> None:
        """Run work through fenced finalization, then release process state."""
        lease_lost = False

        async def _work_and_drain(active_session: RunLeaseSession) -> None:
            try:
                await work(active_session)
            finally:
                await self._drain_checkpoints(active_session.run.run_id)

        try:
            await self._run_orchestrator.execute(session, _work_and_drain)
        except RunLeaseLostError as exc:
            lease_lost = True
            self._logger.error("run_failed", run_id=session.run.run_id, error=str(exc))
        except Exception as exc:
            self._logger.error("run_failed", run_id=session.run.run_id, error=str(exc))
        finally:
            self._finish_tracking(session.run)
            try:
                if stack is not None:
                    await stack.aclose()
            finally:
                lock.release()
        if lease_lost and session.kind == "scan":
            await self.recover_stale_runs()

    async def _execute_unpersisted_run(
        self,
        lock: asyncio.Lock,
        run: PipelineRun,
        service_call: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a dry-run preview without any lease or pipeline-row write."""
        try:
            await service_call()
            if run.status == "running":
                self._run_orchestrator.finish_unpersisted(run, "succeeded")
        except Exception as exc:
            self._logger.error("run_failed", run_id=run.run_id, error=str(exc))
            self._run_orchestrator.finish_unpersisted(run, "failed")
        finally:
            self._finish_tracking(run)
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
    def _source_label_before_resolution(
        name_or_specs: str | list[SourceSpec],
    ) -> str:
        """Label a run before source construction can perform store work."""
        if isinstance(name_or_specs, str):
            return name_or_specs
        return run_source_name(name_or_specs)

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

    def _finish_tracking(self, run: PipelineRun) -> None:
        """Broadcast a final snapshot and release in-process tracking only."""
        self._progress.broadcast(run.run_id, run)
        self._progress.close(run.run_id)
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
        return self._progress.subscribe(run_id, active=run_id in self._active)

    def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[PipelineRun | object]
    ) -> None:
        """Remove a subscriber queue registered via subscribe().

        Args:
            run_id: Run the queue was subscribed to.
            queue: The queue returned by subscribe().
        """
        self._progress.unsubscribe(run_id, queue)

    def _make_progress(
        self, run_id: str, session: RunLeaseSession | None = None
    ) -> Callable[[PipelineRun], None]:
        """Broadcast progress and checkpoint persisted runs."""
        broadcast = self._progress.callback(run_id)

        def _callback(run: PipelineRun) -> None:
            broadcast(run)
            if (
                session is None
                or session.lease_lost
                or not _should_checkpoint_evaluation_progress(run)
            ):
                return
            task = asyncio.create_task(self._run_orchestrator.checkpoint(session))
            self._checkpoint_tasks.setdefault(run_id, set()).add(task)
            task.add_done_callback(
                lambda completed: self._checkpoint_done(run_id, completed)
            )

        return _callback

    def _checkpoint_done(self, run_id: str, task: asyncio.Task[bool]) -> None:
        tasks = self._checkpoint_tasks.get(run_id)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                self._checkpoint_tasks.pop(run_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.warning("run_checkpoint_failed", error=str(exc))

    async def _drain_checkpoints(self, run_id: str) -> None:
        tasks = tuple(self._checkpoint_tasks.get(run_id, ()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_run(self, run_id: str) -> bool:
        """Stop a local task or atomically clear a durable stale run.

        Args:
            run_id: Pipeline run identity to stop.

        Returns:
            True when the run was stopped.
        """
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return True
        if isinstance(self._store, RecoverableRunLeaseStore):
            return await self._store.stop_pipeline_run(run_id, now=datetime.now(UTC))
        return False

    async def recover_stale_runs(self) -> int:
        """Recover expired durable leases when the store supports it.

        Returns:
            Number of stale runs recovered.
        """
        if isinstance(self._store, RecoverableRunLeaseStore):
            recovered = await self._store.recover_expired_run_leases(
                now=datetime.now(UTC)
            )
            for old in recovered:
                if (
                    old.kind != "scan"
                    or old.restart_count != 0
                    or not self._auto_restart_allowed(old.source)
                ):
                    continue
                try:
                    replacement = await self.trigger_scan(
                        old.source, restart_count=old.restart_count + 1
                    )
                except Exception as exc:
                    self._logger.warning(
                        "run_auto_restart_skipped",
                        run_id=old.run_id,
                        source=old.source,
                        error=str(exc),
                    )
                    continue
                await self._store.link_restarted_run(old.run_id, replacement)
            return len(recovered)
        return 0


__all__ = ["RUN_DONE", "ActiveRun", "RunConflictError", "RunManager", "SourceResolver"]


def _should_checkpoint_evaluation_progress(run: PipelineRun) -> bool:
    """Keep per-job SSE live while bounding durable evaluation writes."""
    if run.source != "evaluate":
        return True
    if run.progress_stage == "stage_a":
        processed, total = run.stage_a_processed, run.stage_a_total
    elif run.progress_stage == "stage_b":
        processed, total = run.stage_b_processed, run.stage_b_total
    else:
        return True
    return (
        processed == 0
        or processed % _EVALUATION_CHECKPOINT_INTERVAL == 0
        or (total is not None and processed == total)
    )
