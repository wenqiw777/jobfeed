"""Shared fenced run lifecycle for CLI and web scan/evaluate paths."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeAlias
from uuid import UUID, uuid4

from jobfeed.domain.errors import RunConflictError, RunLeaseLostError
from jobfeed.domain.models import PipelineRun
from jobfeed.ports.run_leases import RunKind, RunLeaseStore

Clock: TypeAlias = Callable[[], datetime]
RunIdFactory: TypeAlias = Callable[[], UUID]
RunWork: TypeAlias = Callable[["RunLeaseSession"], Awaitable[None]]

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
# A SQLite renewal can spend up to five seconds behind another writer. Keep
# retrying transient store errors within the three-minute lease window instead
# of declaring the fence lost after only a few busy timeouts.
_HEARTBEAT_RENEW_ATTEMPTS = 20
_HEARTBEAT_RETRY_DELAY_SECONDS = 1.0


@dataclass
class RunLeaseSession:
    """One acquired fencing token plus its scheduling and heartbeat state."""

    run: PipelineRun
    kind: RunKind
    owner_id: str
    generation: int
    _store: RunLeaseStore = field(repr=False)
    _clock: Clock = field(repr=False)
    _heartbeat_interval_seconds: float = field(repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _started: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _lost: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _heartbeat_task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def heartbeat_running(self) -> bool:
        """Return whether the heartbeat loop started and has not stopped.

        Returns:
            True while the background heartbeat task is alive.
        """
        task = self._heartbeat_task
        return self._started.is_set() and task is not None and not task.done()

    @property
    def lease_lost(self) -> bool:
        """Return whether ownership is no longer safe to assume.

        Returns:
            True after a rejected or failed renewal.
        """
        return self._lost.is_set()

    def ensure_active(self) -> None:
        """Reject scheduling after heartbeat renewal has failed.

        Raises:
            RunLeaseLostError: If this worker can no longer schedule work.
        """
        if self.lease_lost:
            raise RunLeaseLostError(f"{self.kind} run {self.run.run_id} lost its lease")

    def _start_heartbeat(self) -> None:
        self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def _wait_until_started(self) -> None:
        await self._started.wait()

    async def _heartbeat(self) -> None:
        self._started.set()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._heartbeat_interval_seconds
                )
            except TimeoutError:
                renewed = await self._renew_with_transient_retry()
                if renewed is None:
                    return
                if not renewed:
                    self._lost.set()
                    return

    async def _renew_with_transient_retry(self) -> bool | None:
        """Renew once, retrying bounded store exceptions but not fence rejection."""
        for attempt in range(_HEARTBEAT_RENEW_ATTEMPTS):
            try:
                return await self._store.renew_run_lease(
                    kind=self.kind,
                    owner_id=self.owner_id,
                    run_id=self.run.run_id,
                    generation=self.generation,
                    now=_aware_utc(self._clock()),
                )
            except Exception:
                if attempt + 1 == _HEARTBEAT_RENEW_ATTEMPTS:
                    return False
                retry_delay = min(
                    _HEARTBEAT_RETRY_DELAY_SECONDS,
                    self._heartbeat_interval_seconds,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=retry_delay)
                except TimeoutError:
                    continue
                return None
        return False  # pragma: no cover

    async def _stop_heartbeat(self) -> None:
        self._stop.set()
        if self._heartbeat_task is not None:
            await self._heartbeat_task


class RunLeaseOrchestrator:
    """Create, heartbeat, and fenced-finalize pipeline runs."""

    def __init__(
        self,
        store: RunLeaseStore,
        *,
        clock: Clock | None = None,
        owner_id: UUID | None = None,
        run_id_factory: RunIdFactory | None = None,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        """Create orchestration with injectable time and UUID identities."""
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._store = store
        self._clock = clock or _utc_now
        self._owner_id = str(owner_id or uuid4())
        self._run_id_factory = run_id_factory or uuid4
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    def new_unpersisted_run(self, source: str) -> PipelineRun:
        """Create an in-memory run for a dry-run path without lease writes.

        Args:
            source: Display label for the preview run.

        Returns:
            A running PipelineRun that has not touched the store.
        """
        return PipelineRun(
            run_id=str(self._run_id_factory()),
            started_at=_aware_utc(self._clock()),
            source=source,
        )

    def finish_unpersisted(self, run: PipelineRun, status: str) -> None:
        """Finish an in-memory dry run using the injected UTC clock.

        Args:
            run: Preview run to mutate in memory.
            status: Terminal status to expose to the caller.
        """
        run.status = status
        run.finished_at = _aware_utc(self._clock())

    async def start(
        self, kind: RunKind, source: str, *, restart_count: int = 0
    ) -> RunLeaseSession:
        """Atomically start a persisted run and its heartbeat before returning.

        Args:
            kind: Exclusive lease kind.
            source: Pipeline history display label.

        Returns:
            Acquired session whose heartbeat task has started.

        Raises:
            RunConflictError: If another live owner holds the kind.
            ValueError: If the injected clock returns a naive timestamp.
        """
        now = _aware_utc(self._clock())
        run = PipelineRun(
            run_id=str(self._run_id_factory()),
            started_at=now,
            source=source,
            restart_count=restart_count,
        )
        generation = await self._store.start_run_with_lease(
            run,
            kind=kind,
            owner_id=self._owner_id,
            now=now,
        )
        if generation is None:
            raise RunConflictError(f"A {kind} run is already active")
        session = RunLeaseSession(
            run=run,
            kind=kind,
            owner_id=self._owner_id,
            generation=generation,
            _store=self._store,
            _clock=self._clock,
            _heartbeat_interval_seconds=self._heartbeat_interval_seconds,
        )
        session._start_heartbeat()
        await session._wait_until_started()
        return session

    async def execute(self, session: RunLeaseSession, work: RunWork) -> PipelineRun:
        """Run leased work and persist its terminal state with the same fence.

        Args:
            session: Previously acquired fencing session.
            work: Scan or evaluate operation guarded by the session.

        Returns:
            Fenced terminal pipeline run.

        Raises:
            RunLeaseLostError: If ownership is lost before finalization.
        """
        try:
            session.ensure_active()
            await work(session)
            session.ensure_active()
        except BaseException as exc:
            _record_failure(session, exc)
            await self._finalize(session, "failed")
            raise
        finalized = await self._finalize(session, "succeeded")
        if not finalized:
            raise RunLeaseLostError(
                f"{session.kind} run {session.run.run_id} lost its lease"
            )
        return session.run

    async def run(
        self,
        kind: RunKind,
        source: str,
        work: RunWork,
    ) -> PipelineRun:
        """Atomically start, execute, and fenced-finalize one pipeline run.

        Args:
            kind: Exclusive lease kind.
            source: Pipeline history display label.
            work: Scan or evaluate operation guarded by the new session.

        Returns:
            Fenced terminal pipeline run.
        """
        session = await self.start(kind, source)
        return await self.execute(session, work)

    async def fail(
        self, session: RunLeaseSession, exc: BaseException | None = None
    ) -> bool:
        """Fenced-finalize a started session that failed before work began.

        Args:
            session: Acquired session whose setup failed.
            exc: Optional setup exception to classify for run history.

        Returns:
            True only when the session still owned its fence.
        """
        if exc is not None:
            _record_failure(session, exc)
        return await self._finalize(session, "failed")

    async def checkpoint(self, session: RunLeaseSession) -> bool:
        """Persist current counters without releasing the live lease.

        Args:
            session: Acquired run whose current snapshot should be saved.

        Returns:
            True when saved, or for a transition-only test/legacy store that
            does not implement durable checkpoints.
        """
        session.ensure_active()
        checkpoint = getattr(self._store, "checkpoint_run_with_lease", None)
        if checkpoint is None:
            return True
        return bool(
            await checkpoint(
                session.run,
                kind=session.kind,
                owner_id=session.owner_id,
                generation=session.generation,
                now=_aware_utc(self._clock()),
            )
        )

    async def _finalize(self, session: RunLeaseSession, status: str) -> bool:
        await session._stop_heartbeat()
        now = _aware_utc(self._clock())
        if status == "failed" or session.run.status == "running":
            session.run.status = status
        session.run.finished_at = now
        return await self._store.finalize_run_with_lease(
            session.run,
            kind=session.kind,
            owner_id=session.owner_id,
            generation=session.generation,
            now=now,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("run lease clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


_SECRET = re.compile(r"(?i)(authorization|api[_-]?key|token|password)(\s*[:=]\s*)\S+")


def _record_failure(session: RunLeaseSession, exc: BaseException) -> None:
    run = session.run
    if isinstance(exc, asyncio.CancelledError):
        run.failure_code = "user_stopped"
        run.failure_message = "Run stopped by user"
    elif isinstance(exc, RunLeaseLostError):
        run.failure_code = "interrupted"
        run.failure_message = "Run interrupted after its worker stopped responding"
    else:
        run.failure_code = "runtime_error"
        message = " ".join(str(exc).split()) or type(exc).__name__
        run.failure_message = _SECRET.sub(r"\1\2[redacted]", message)[:300]
    run.failed_stage = run.progress_stage or run.scan_phase or session.kind
    run.failed_source = run.scan_source or run.source


__all__ = ["RunLeaseOrchestrator", "RunLeaseSession"]
