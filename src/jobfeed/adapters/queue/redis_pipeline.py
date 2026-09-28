"""Fenced Redis Streams work journal with durable inputs and completed results.

The API's leased source workers claim their named tasks. Streams retain pending
work; replay reconstructs callbacks from the saved discovery payload, not pickles.
No arbitrary executable content is stored in Redis.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import quote

from redis.asyncio import Redis
from redis.exceptions import ConnectionError, ResponseError, TimeoutError

_COMMAND_ATTEMPTS = 3

_START = """
local old = tonumber(redis.call('GET', KEYS[1]) or '-1')
if old > tonumber(ARGV[1]) then return redis.error_reply('pipeline ownership lost') end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[2], ARGV[2])
return 1
"""
_ENQUEUE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return redis.error_reply('pipeline ownership lost')
end
local result = redis.call('GET', KEYS[2])
if result and ARGV[4] == 'retry_errors' then
  local decoded = cjson.decode(result)
  if decoded.error and decoded.error ~= cjson.null and not decoded.warning then
    redis.call('RPUSH', KEYS[5], cjson.encode(decoded.jobs or {}))
    redis.call('DEL', KEYS[2], KEYS[3])
    result = false
  end
end
if result then
  redis.call('UNLINK', KEYS[5])
  return {'done', result}
end
local id = redis.call('GET', KEYS[3])
if not id then
  if redis.call('XLEN', KEYS[4]) >= 4096 then
    return redis.error_reply('pipeline backlog full')
  end
  id = redis.call('XADD', KEYS[4], '*', 'task', ARGV[2], 'payload', ARGV[3])
  redis.call('SET', KEYS[3], id)
end
return {'pending', id}
"""
_COMPLETE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return redis.error_reply('pipeline ownership lost')
end
redis.call('SET', KEYS[2], ARGV[2])
redis.call('UNLINK', KEYS[4])
redis.call('XACK', KEYS[3], 'workers', ARGV[3])
redis.call('XDEL', KEYS[3], ARGV[3])
return 1
"""
_PARTIAL = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return redis.error_reply('pipeline ownership lost')
end
return redis.call('RPUSH', KEYS[2], ARGV[2])
"""


class RedisPipeline:
    """One leased scan's durable steps; all payloads are JSON values."""

    def __init__(self, client: Redis, *, namespace: str = "jobfeed") -> None:
        self.client = client
        self._raw_command = cast(Callable[..., Awaitable[Any]], client.execute_command)
        self._command = self._execute_command
        self.namespace = namespace
        self.root = ""
        self.prefix = ""
        self.generation = ""
        self._slots = asyncio.Semaphore(64)
        self.writer_lock = asyncio.Lock()

    async def _execute_command(self, *args: Any) -> Any:
        # These fenced operations are idempotent even if Redis applied the write
        # but its reply was lost. RPUSH partials are deliberately excluded.
        safe = args[0] in {"LRANGE", "XCLAIM"} or (
            args[0] == "EVAL" and args[1] in {_START, _ENQUEUE, _COMPLETE}
        )
        for attempt in range(_COMMAND_ATTEMPTS):
            try:
                return await self._raw_command(*args)
            except (ConnectionError, TimeoutError):
                if not safe or attempt == _COMMAND_ATTEMPTS - 1:
                    raise
                logging.getLogger(__name__).warning(
                    "Redis journal reply unavailable; retrying safe %s attempt %s",
                    args[0],
                    attempt + 2,
                )
                await asyncio.sleep(0.25 * (attempt + 1))
        raise AssertionError("Redis retry loop exhausted")

    async def start(
        self, run_id: str, *, generation: int, resume_from: str | None = None
    ) -> None:
        """Acquire a journal after obtaining the database run lease.

        Args:
            run_id: Identifier of the current scan run.
            generation: Fencing generation of the acquired lease.
            resume_from: Previous run whose journal should be resumed.

        Raises:
            RuntimeError: If the requested resume journal is missing.
            ResponseError: If Redis rejects stream-group creation.
        """
        root = (
            await self.client.get(f"{self.namespace}:run:{resume_from}")
            if resume_from
            else run_id
        )
        if root is None:
            raise RuntimeError(
                "Redis pipeline to resume is missing; refusing a silent rescan"
            )
        self.root = str(root)
        self.prefix = f"{self.namespace}:pipeline:{quote(self.root, safe='')}:"
        self.generation = str(generation)
        await self._command(
            "EVAL",
            _START,
            2,
            self.prefix + "owner",
            f"{self.namespace}:run:{run_id}",
            self.generation,
            self.root,
        )
        try:
            await self.client.xgroup_create(
                self.prefix + "tasks", "workers", id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        if resume_from:
            # Terminal unfinished journals have a bounded retry window. Once
            # the replacement owns the root, its live work must not expire.
            async with self.client.pipeline(transaction=False) as commands:
                count = 0
                async for key in self.client.scan_iter(
                    match=self.prefix + "*", count=500
                ):
                    commands.persist(key)
                    count += 1
                    if count % 500 == 0:
                        await commands.execute()
                await commands.execute()

    async def step(
        self,
        name: str,
        payload: Any,
        work: Callable[[Any], Awaitable[Any]],
        *,
        retry_errors: bool = False,
    ) -> Any:
        """Persist an operation and acknowledge it only after saving its output.

        Args:
            name: Stable operation or source name.
            payload: JSON-compatible operation input.
            work: Async operation to execute with saved input.
            retry_errors: Whether prior error results may be retried.

        Returns:
            Saved or freshly computed output.

        Raises:
            RuntimeError: If the accepted journal task is missing.
        """
        async with self._slots:
            key = self.prefix + "step:" + quote(name, safe="")
            status, value = await self._command(
                "EVAL",
                _ENQUEUE,
                5,
                self.prefix + "owner",
                key + ":result",
                key + ":id",
                self.prefix + "tasks",
                self.prefix + "partial:" + quote(name, safe=""),
                self.generation,
                name,
                json.dumps(payload),
                "retry_errors" if retry_errors else "",
            )
            if status == "done":
                return json.loads(value)
            # Exact task claiming is safe because DB lease + generation fencing
            # grant this worker exclusive ownership of this pipeline generation.
            rows = await self._command(
                "XCLAIM",
                self.prefix + "tasks",
                "workers",
                self.generation,
                0,
                value,
                "FORCE",
            )
            if not rows:
                raise RuntimeError("Accepted Redis task is missing")
            saved_payload = json.loads(rows[0][1]["payload"])
            result = await work(saved_payload)
            if retry_errors:
                result = {**result, "generation": self.generation}
            await self._command(
                "EVAL",
                _COMPLETE,
                4,
                self.prefix + "owner",
                key + ":result",
                self.prefix + "tasks",
                self.prefix + "partial:" + quote(name, safe=""),
                self.generation,
                json.dumps(result),
                value,
            )
            return result

    async def save_partial(self, name: str, rows: list[dict[str, Any]]) -> None:
        """Persist a browser batch before accepting its receipt.

        Args:
            name: Stable operation or source name.
            rows: Collected source rows.
        """
        if rows:
            await self._command(
                "EVAL",
                _PARTIAL,
                2,
                self.prefix + "owner",
                self.prefix + "partial:" + quote(name, safe=""),
                self.generation,
                json.dumps(rows),
            )

    async def load_partial(self, name: str) -> list[dict[str, Any]]:
        """Load previously persisted browser batches.

        Args:
            name: Stable operation or source name.

        Returns:
            Rows from all saved batches, in journal order.
        """
        chunks = await self._command(
            "LRANGE", self.prefix + "partial:" + quote(name, safe=""), 0, -1
        )
        return [row for chunk in chunks for row in json.loads(chunk)]

    async def pending_count(self) -> int:
        """Count unfinished journal tasks.

        Returns:
            Number of tasks still awaiting completion.
        """
        return int(await self.client.xlen(self.prefix + "tasks"))

    async def assert_drained(self) -> None:
        """Verify all discovery and source work has finished.

        Raises:
            RuntimeError: If unfinished tasks remain.
        """
        pending = await self.pending_count()
        if pending:
            raise RuntimeError(
                f"Redis pipeline has {pending} pending tasks; retry to resume"
            )
