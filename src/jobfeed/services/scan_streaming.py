"""Bounded source-save consumer, independent of browser result delivery."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.models import JobPosting


class ScanBatchWriter:
    """Keep browser batch delivery moving while committed writes feed deduplication."""

    def __init__(
        self, save: Callable[[list[JobPosting], str | None], Awaitable[None]]
    ) -> None:
        self.save = save
        self.queue: asyncio.Queue[tuple[list[JobPosting], str | None] | None] = (
            asyncio.Queue(maxsize=10)
        )
        self.seen: set[tuple[str, str]] = set()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "ScanBatchWriter":
        """Start one consumer whose lifetime is owned by the source fetch."""
        self._task = asyncio.create_task(self._consume(), name="scan-batch-writer")
        return self

    async def __aexit__(self, kind: Any, error: Any, traceback: Any) -> None:
        """Finish accepted batches on success, cancel owned writes on interruption."""
        assert self._task is not None
        try:
            if kind is None or (
                isinstance(error, Exception)
                and not isinstance(error, RunLeaseLostError)
            ):
                await self._put(None)
                await self._task
        finally:
            if not self._task.done():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def submit(
        self, jobs: list[JobPosting], *, generation: str | None = None
    ) -> None:
        """Queue a new batch; a slow database does not block the next fetch directly.

        Args:
            jobs: Observed source postings in the arriving batch.
            generation: Original immutable journal attempt owning this batch.
        """
        fresh = self.unseen(jobs)
        if fresh:
            await self._put((fresh, generation))
            self.seen.update((job.platform, job.canonical_id) for job in fresh)

    def unseen(self, jobs: list[JobPosting]) -> list[JobPosting]:
        """Return each source identity once, excluding batches already accepted.

        Args:
            jobs: Posting batch supplied by a source or durable replay.

        Returns:
            Unique source records whose identities have not been queued.
        """
        unique = {(job.platform, job.canonical_id): job for job in jobs}
        return [job for key, job in unique.items() if key not in self.seen]

    async def _put(self, value: tuple[list[JobPosting], str | None] | None) -> None:
        assert self._task is not None
        put = asyncio.create_task(self.queue.put(value))
        try:
            await asyncio.wait({put, self._task}, return_when=asyncio.FIRST_COMPLETED)
            if self._task.done():
                self._task.result()
                if value is not None:
                    raise RuntimeError("source batch writer already stopped")
            await put
        finally:
            if not put.done():
                put.cancel()
                await asyncio.gather(put, return_exceptions=True)

    async def _consume(self) -> None:
        while True:
            batch = await self.queue.get()
            try:
                if batch is None:
                    return
                jobs, generation = batch
                await self.save(jobs, generation)
            finally:
                self.queue.task_done()
