"""In-process bridge between a Jobright scan and the Chrome extension."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from jobfeed.ports.source import SourceFetchProgress
from jobfeed.services.pipeline_context import current_pipeline

ProgressCallback = Callable[[SourceFetchProgress], None]
DiscoveryCallback = Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]]
_DISCOVERY_PAGE_LIMIT = 25
_NATIVE_ID_LIMIT = 256


class JobrightBridgeError(RuntimeError):
    """An extension task failed, retaining already-received job records."""

    def __init__(
        self,
        message: str,
        partial_jobs: list[dict[str, Any]] | None = None,
        *,
        warning: bool = False,
    ):
        super().__init__(message)
        self.partial_jobs = partial_jobs or []
        self.warning = warning


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
    source: str
    on_progress: ProgressCallback
    jobs: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    on_batch: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None
    on_discovery: DiscoveryCallback | None = None


class JobrightBridge:
    """Coordinate one local extension connection and active scan tasks."""

    def __init__(self) -> None:
        self._commands: asyncio.Queue[dict[str, object]] | None = None
        self._pending: dict[str, _PendingScan] = {}
        self._retired_tasks: deque[str] = deque(maxlen=256)
        self._active_lanes: set[str] = set()
        self.supported_sources: frozenset[str] = frozenset()

    @property
    def connected(self) -> bool:
        """Whether a Chrome extension currently owns the bridge.

        Returns:
            True when exactly one extension owns the command queue.
        """
        return self._commands is not None

    def connect(self, sources: list[str] | None = None) -> JobrightBridgeConnection:
        """Register the sole extension connection for this process.

        Returns:
            Handle used by the WebSocket sender to receive commands.

        Raises:
            JobrightBridgeError: If another extension is already connected.

        Args:
            sources: Supported source names advertised by the extension.
        """
        if self._commands is not None:
            raise JobrightBridgeError("Jobright Chrome extension is already connected")
        self.supported_sources = frozenset(sources or ["jobright"])
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
        self.supported_sources = frozenset()
        error = JobrightBridgeError("Jobright Chrome extension disconnected")
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(
                    JobrightBridgeError(str(error), list(pending.jobs))
                )

    async def run_scan(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Allow independent sources while rejecting same-lane contention.

        Args:
            kwargs: Extension scan request options.

        Returns:
            Source rows returned by the extension.

        Raises:
            JobrightBridgeError: If the extension is unavailable or the scan fails.
        """
        source = kwargs.get("source", "jobright")
        lane = "enrichment" if source in {"github-jd", "tiktok"} else source
        if lane in self._active_lanes:
            raise JobrightBridgeError(f"{lane} browser lane is busy")
        self._active_lanes.add(lane)
        try:
            if current_pipeline.get() is not None:
                return await self._durable_run_scan(kwargs)
            return await self._run_scan(**kwargs)
        finally:
            self._active_lanes.discard(lane)

    async def _durable_run_scan(self, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        pipeline = current_pipeline.get()
        assert pipeline is not None
        payload = {
            key: value
            for key, value in kwargs.items()
            if key not in {"on_progress", "on_discovery"}
        }
        source = payload.get("source", "jobright")
        identity = {
            key: payload.get(key)
            for key in ("source", "query", "sort", "search_url", "filters")
        }
        if payload.get("targets"):
            identity["ids"] = [item["id"] for item in payload["targets"]]
        name = "bridge:" + json.dumps(identity, sort_keys=True)

        async def execute(saved: dict[str, Any]) -> dict[str, Any]:
            cached = await pipeline.load_partial(name)
            options = dict(saved)
            if source in {"github-jd", "tiktok"}:
                completed = {(str(row.get("id")), row.get("url")) for row in cached}
                options["targets"] = [
                    t
                    for t in options.get("targets", [])
                    if (str(t["id"]), t["url"]) not in completed
                ]
                options["max_jobs"] = len(options["targets"])
                if not options["targets"]:
                    return {"jobs": cached, "error": None}

            async def persist(rows: list[dict[str, Any]]) -> None:
                await pipeline.save_partial(name, rows)

            error = None
            warning = False
            try:
                rows = await self._run_scan(
                    **options,
                    on_progress=kwargs["on_progress"],
                    on_batch=persist,
                    on_discovery=kwargs.get("on_discovery"),
                    cached_jobs=cached,
                )
            except JobrightBridgeError as exc:
                rows, error = exc.partial_jobs, str(exc)
                warning = exc.warning
            combined = {}
            for row in cached + rows:
                identifier = (
                    _job_id(row) if source == "jobright" else str(row.get("id"))
                )
                combined[identifier] = row
            return {"jobs": list(combined.values()), "error": error, "warning": warning}

        result = await pipeline.step(name, payload, execute, retry_errors=True)
        if result["error"]:
            raise JobrightBridgeError(
                result["error"], result["jobs"], warning=result.get("warning", False)
            )
        return cast(list[dict[str, Any]], result["jobs"])

    async def _run_scan(  # noqa: PLR0913
        self,
        *,
        max_jobs: int,
        batch_size: int,
        pacing_s: float,
        timeout_s: float,
        on_progress: ProgressCallback,
        source: str = "jobright",
        query: str = "",
        sort: str = "relevance",
        filters: dict[str, list[str]] | None = None,
        search_url: str | None = None,
        targets: list[dict[str, str]] | None = None,
        on_batch: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_discovery: DiscoveryCallback | None = None,
        cached_jobs: list[dict[str, Any]] | None = None,
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
        if source not in self.supported_sources:
            raise JobrightBridgeError(
                f"Please reload the Jobfeed extension to scan {source}"
            )
        if (
            on_discovery is not None
            and "discovery-gate-v1" not in self.supported_sources
        ):
            raise JobrightBridgeError(
                "Please reload the Jobfeed extension for ID-first discovery"
            )
        if search_url and "linkedin-search-results" not in self.supported_sources:
            raise JobrightBridgeError(
                "Please reload the Jobfeed extension for LinkedIn search-results URLs"
            )
        loop = asyncio.get_running_loop()
        task_id = str(uuid4())
        future: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
        self._pending[task_id] = _PendingScan(
            future=future,
            max_jobs=max_jobs,
            source=source,
            on_progress=on_progress,
            on_batch=on_batch,
            on_discovery=on_discovery,
        )
        await self._commands.put(
            {
                "type": "start_scan" if source == "jobright" else "start_board_scan",
                **(
                    {"source": source, "query": query, "sort": sort}
                    if source != "jobright"
                    else {}
                ),
                **({"filters": filters} if filters is not None else {}),
                **({"search_url": search_url} if search_url else {}),
                **({"targets": targets} if targets is not None else {}),
                "task_id": task_id,
                **(
                    {"discovery_gate": True, "cached_jobs": cached_jobs or []}
                    if on_discovery is not None
                    else {}
                ),
                "max_jobs": max_jobs,
                "batch_size": batch_size,
                "pacing_ms": round(pacing_s * 1000),
            }
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_s)
        except TimeoutError as exc:
            await self._commands.put({"type": "cancel", "task_id": task_id})
            raise JobrightBridgeError(
                "Jobright Chrome scan timed out", list(self._pending[task_id].jobs)
            ) from exc
        except asyncio.CancelledError:
            await self._commands.put({"type": "cancel", "task_id": task_id})
            raise
        finally:
            self._retired_tasks.append(task_id)
            self._pending.pop(task_id, None)
            if not future.done():
                future.cancel()

    async def _notify_batch(
        self, task_id: str, pending: _PendingScan, before: int
    ) -> None:
        if pending.on_batch is None:
            return
        try:
            await pending.on_batch(pending.jobs[before:])
        except Exception as exc:
            if not pending.future.done():
                pending.future.set_exception(exc)
            if self._commands is not None:
                await self._commands.put({"type": "cancel", "task_id": task_id})

    async def receive(self, message: dict[str, object]) -> None:
        """Accept one batch, completion, or error message from the extension.

        Args:
            message: Versioned extension response payload.

        Raises:
            JobrightBridgeError: If the task or message type is unknown.
        """
        task_id = message.get("task_id")
        if isinstance(task_id, str) and task_id in self._retired_tasks:
            return  # A batch/completion can already be in flight when cancelled.
        if not isinstance(task_id, str) or task_id not in self._pending:
            raise JobrightBridgeError("unknown Jobright bridge task")
        pending = self._pending[task_id]
        if pending.future.done():
            return
        message_type = message.get("type")
        if message_type == "discovery":
            await self._receive_discovery(task_id, pending, message)
            return
        if message_type == "batch":
            before = len(pending.jobs)
            self._receive_batch(pending, message.get("jobs"))
            await self._notify_batch(task_id, pending, before)
            return
        if message_type == "complete":
            if isinstance(message.get("warning"), str) and message["warning"]:
                pending.future.set_exception(
                    JobrightBridgeError(
                        str(message["warning"]), list(pending.jobs), warning=True
                    )
                )
            else:
                pending.future.set_result(list(pending.jobs))
            return
        if message_type == "error":
            detail = message.get("error")
            text = detail if isinstance(detail, str) else "Jobright extension failed"
            if not pending.future.done():
                pending.future.set_exception(
                    JobrightBridgeError(text, list(pending.jobs))
                )
            return
        raise JobrightBridgeError(f"unknown Jobright bridge message: {message_type!r}")

    async def _receive_discovery(
        self, task_id: str, pending: _PendingScan, message: dict[str, object]
    ) -> None:
        rows, request_id = message.get("rows"), message.get("request_id")
        if (
            pending.on_discovery is None
            or not isinstance(request_id, str)
            or not isinstance(rows, list)
            or len(rows) > _DISCOVERY_PAGE_LIMIT
            or any(
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"]
                or len(row["id"]) > _NATIVE_ID_LIMIT
                for row in rows
            )
        ):
            raise JobrightBridgeError("Invalid discovery lookup")
        commands = self._commands
        if commands is None:
            raise JobrightBridgeError("Extension disconnected during lookup")
        response = {
            "type": "discovery_result",
            "task_id": task_id,
            "request_id": request_id,
        }
        try:
            decision = await pending.on_discovery(rows)
            await commands.put({**response, **decision})
        except Exception:
            # The worker reports its task error after receiving this decision.
            # Keep that task registered until then, avoiding a late error that
            # would otherwise disconnect the other source lanes.
            await commands.put(
                {**response, "error": "Database discovery lookup failed"}
            )

    @staticmethod
    def _receive_batch(pending: _PendingScan, value: object) -> None:
        if not isinstance(value, list):
            raise JobrightBridgeError("Jobright batch jobs must be a list")
        last_id: str | None = None
        for item in value:
            if not isinstance(item, dict):
                continue
            if len(pending.jobs) >= pending.max_jobs:
                break
            job_id = (
                _job_id(item)
                if pending.source == "jobright"
                else str(item["id"])
                if item.get("source") == pending.source and item.get("id")
                else None
            )
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
