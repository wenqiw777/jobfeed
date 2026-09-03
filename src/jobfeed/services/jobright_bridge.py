"""In-process bridge between a Jobright scan and the Chrome extension."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from jobfeed.ports.source import SourceFetchProgress

ProgressCallback = Callable[[SourceFetchProgress], None]


class JobrightBridgeError(RuntimeError):
    """A Jobright extension task could not be completed."""


class JobrightBridgeConnection:
    """One connected extension's outbound command queue."""

    def __init__(self, commands: asyncio.Queue[dict[str, object]]) -> None:
        self._commands = commands

    async def next_command(self) -> dict[str, object]:
        """Wait for the next command destined for the extension.

        Returns:
            Next versioned command payload for the connected extension.
        """
        return await self._commands.get()


@dataclass
class _PendingScan:
    future: asyncio.Future[list[dict[str, Any]]]
    max_jobs: int
    on_progress: ProgressCallback
    jobs: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)


class JobrightBridge:
    """Coordinate one local extension connection and active scan tasks."""

    def __init__(self) -> None:
        self._commands: asyncio.Queue[dict[str, object]] | None = None
        self._pending: dict[str, _PendingScan] = {}

    @property
    def connected(self) -> bool:
        """Whether a Chrome extension currently owns the bridge.

        Returns:
            True when exactly one extension owns the command queue.
        """
        return self._commands is not None

    def connect(self) -> JobrightBridgeConnection:
        """Register the sole extension connection for this process.

        Returns:
            Handle used by the WebSocket sender to receive commands.

        Raises:
            JobrightBridgeError: If another extension is already connected.
        """
        if self._commands is not None:
            raise JobrightBridgeError("Jobright Chrome extension is already connected")
        self._commands = asyncio.Queue()
        return JobrightBridgeConnection(self._commands)

    def disconnect(self, connection: JobrightBridgeConnection | None = None) -> None:
        """Drop the extension and fail every task waiting on it.

        Args:
            connection: Optional owner handle; stale handles are ignored.
        """
        if connection is not None and connection._commands is not self._commands:
            return
        self._commands = None
        error = JobrightBridgeError("Jobright Chrome extension disconnected")
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(error)

    async def run_scan(
        self,
        *,
        max_jobs: int,
        batch_size: int,
        pacing_s: float,
        timeout_s: float,
        on_progress: ProgressCallback,
    ) -> list[dict[str, Any]]:
        """Send one scan task to Chrome and wait for its completed payload.

        Args:
            max_jobs: Maximum unique recommendations accepted.
            batch_size: Number of recommendations requested per browser call.
            pacing_s: Delay between browser requests in seconds.
            timeout_s: Maximum total scan duration in seconds.
            on_progress: Callback receiving accepted recommendation counts.

        Returns:
            Deduplicated raw recommendation payloads.

        Raises:
            JobrightBridgeError: If disconnected, timed out, or rejected by Chrome.
        """
        if self._commands is None:
            raise JobrightBridgeError("Jobright Chrome extension is not connected")
        loop = asyncio.get_running_loop()
        task_id = str(uuid4())
        future: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
        self._pending[task_id] = _PendingScan(
            future=future,
            max_jobs=max_jobs,
            on_progress=on_progress,
        )
        await self._commands.put(
            {
                "type": "start_scan",
                "task_id": task_id,
                "max_jobs": max_jobs,
                "batch_size": batch_size,
                "pacing_ms": round(pacing_s * 1000),
            }
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_s)
        except TimeoutError as exc:
            await self._commands.put({"type": "cancel", "task_id": task_id})
            raise JobrightBridgeError("Jobright Chrome scan timed out") from exc
        except asyncio.CancelledError:
            await self._commands.put({"type": "cancel", "task_id": task_id})
            raise
        finally:
            self._pending.pop(task_id, None)

    async def receive(self, message: dict[str, object]) -> None:
        """Accept one batch, completion, or error message from the extension.

        Args:
            message: Versioned extension response payload.

        Raises:
            JobrightBridgeError: If the task or message type is unknown.
        """
        task_id = message.get("task_id")
        if not isinstance(task_id, str) or task_id not in self._pending:
            raise JobrightBridgeError("unknown Jobright bridge task")
        pending = self._pending[task_id]
        message_type = message.get("type")
        if message_type == "batch":
            self._receive_batch(pending, message.get("jobs"))
            return
        if message_type == "complete":
            if not pending.future.done():
                pending.future.set_result(list(pending.jobs))
            return
        if message_type == "error":
            detail = message.get("error")
            text = detail if isinstance(detail, str) else "Jobright extension failed"
            if not pending.future.done():
                pending.future.set_exception(JobrightBridgeError(text))
            return
        raise JobrightBridgeError(f"unknown Jobright bridge message: {message_type!r}")

    @staticmethod
    def _receive_batch(pending: _PendingScan, value: object) -> None:
        if not isinstance(value, list):
            raise JobrightBridgeError("Jobright batch jobs must be a list")
        last_id: str | None = None
        for item in value:
            if not isinstance(item, dict):
                continue
            job_id = _job_id(item)
            if job_id is None or job_id in pending.seen_ids:
                continue
            pending.seen_ids.add(job_id)
            pending.jobs.append(item)
            last_id = job_id
            if len(pending.jobs) >= pending.max_jobs:
                break
        pending.on_progress(
            SourceFetchProgress(
                processed=len(pending.jobs),
                total=pending.max_jobs,
                current_job_id=last_id,
            )
        )


def _job_id(item: dict[str, object]) -> str | None:
    result = item.get("jobResult")
    if not isinstance(result, dict):
        return None
    value = result.get("jobId")
    return str(value) if isinstance(value, str | int) and str(value) else None


__all__ = [
    "JobrightBridge",
    "JobrightBridgeConnection",
    "JobrightBridgeError",
]
