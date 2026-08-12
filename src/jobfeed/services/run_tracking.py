"""In-process active-run metadata and progress fan-out."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from jobfeed.domain.models import PipelineRun


@dataclass
class ActiveRun:
    """A currently executing pipeline run."""

    run_id: str
    source: str
    started_at: datetime
    run: PipelineRun


class RunProgressBroker:
    """Broadcast immutable run snapshots to in-process SSE subscribers."""

    def __init__(self, done_sentinel: object) -> None:
        self._done = done_sentinel
        self._subscribers: dict[str, list[asyncio.Queue[PipelineRun | object]]] = {}

    def subscribe(
        self, run_id: str, *, active: bool
    ) -> asyncio.Queue[PipelineRun | object]:
        """Create a queue, immediately closing it when the run is inactive.

        Args:
            run_id: Pipeline identity to observe.
            active: Whether the manager still owns the task.

        Returns:
            Progress queue terminated by the configured sentinel.
        """
        queue: asyncio.Queue[PipelineRun | object] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        if not active:
            queue.put_nowait(self._done)
        return queue

    def unsubscribe(
        self, run_id: str, queue: asyncio.Queue[PipelineRun | object]
    ) -> None:
        """Remove a previously subscribed queue.

        Args:
            run_id: Pipeline identity previously observed.
            queue: Queue returned by subscribe.
        """
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    def callback(self, run_id: str) -> Callable[[PipelineRun], None]:
        """Return a progress callback bound to one run identity.

        Args:
            run_id: Pipeline identity whose snapshots should be broadcast.

        Returns:
            Synchronous service progress callback.
        """

        def _on_progress(run: PipelineRun) -> None:
            self.broadcast(run_id, run)

        return _on_progress

    def broadcast(self, run_id: str, run: PipelineRun) -> None:
        """Send a detached counter snapshot to every current subscriber.

        Args:
            run_id: Pipeline identity being updated.
            run: Mutable counters to copy before enqueueing.
        """
        snapshot = replace(run, dry_run_preview=list(run.dry_run_preview))
        for queue in self._subscribers.get(run_id, []):
            queue.put_nowait(snapshot)

    def close(self, run_id: str) -> None:
        """Close and forget every subscriber for a completed run.

        Args:
            run_id: Completed pipeline identity.
        """
        for queue in self._subscribers.pop(run_id, []):
            queue.put_nowait(self._done)


__all__ = ["ActiveRun", "RunProgressBroker"]
