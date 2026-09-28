"""Redis scan journal lifecycle, replay context and terminal retention."""

from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import quote

from redis.asyncio import Redis

from jobfeed.adapters.queue.redis_pipeline import RedisPipeline
from jobfeed.domain.models import PipelineRun
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.pipeline import PipelineStore
from jobfeed.ports.store import JobStore
from jobfeed.services.pipeline_context import current_pipeline

_UNFINISHED_RETENTION_SECONDS = 24 * 60 * 60
_FINISHED_MAPPING_RETENTION_SECONDS = 7 * 24 * 60 * 60


class RedisScanJournal:
    """Own Redis resources for one scan and keep retry data bounded."""

    def __init__(
        self, store: JobStore, logger: JobfeedLogger, *, url: str, namespace: str
    ) -> None:
        self.store = store
        self.logger = logger
        self._redis_url = url
        self._redis_namespace = namespace

    async def release(self, run: PipelineRun) -> None:
        """Release drained journals and bound unfinished ones to a retry window.

        Incomplete work remains recoverable for one day after a terminal run.
        Cleanup failure must not change committed scan success.

        Args:
            run: Persisted terminal scan whose journal may be released.
        """
        try:
            store = cast(PipelineStore, self.store)
            saved = await self.store.get_pipeline_run(run.run_id)
            if saved is None or saved.status not in {"succeeded", "failed"}:
                return
            async with Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=10,
            ) as client:
                mapping = f"{self._redis_namespace}:run:{run.run_id}"
                root = await client.get(mapping)
                if not root:
                    return
                prefix = f"{self._redis_namespace}:pipeline:{quote(root, safe='')}:"
                drained = bool(
                    await store.get_state(f"redis-pipeline-drained:{run.run_id}")
                )
                release = drained and not await client.xlen(prefix + "tasks")
                async with client.pipeline(transaction=False) as commands:
                    count = 0
                    async for key in client.scan_iter(match=prefix + "*", count=500):
                        if release:
                            commands.unlink(key)
                        else:
                            commands.expire(key, _UNFINISHED_RETENTION_SECONDS, nx=True)
                        count += 1
                        if count % 500 == 0:
                            await commands.execute()
                    commands.expire(
                        mapping,
                        _FINISHED_MAPPING_RETENTION_SECONDS
                        if release
                        else _UNFINISHED_RETENTION_SECONDS,
                        nx=True,
                    )
                    await commands.execute()
        except Exception as exc:
            self.logger.warning("redis_completed_retention_deferred", error=str(exc))

    async def run(
        self,
        run: PipelineRun,
        work: Callable[[], Awaitable[None]],
        *,
        generation: int,
    ) -> None:
        """Run scan work in its durable journal context.

        Args:
            run: Current run and its optional replay parent.
            work: Source work invoked once the journal is ready.
            generation: Active fencing generation.

        Raises:
            RuntimeError: If source work fails or accepted work remains undrained.
        """
        async with Redis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        ) as client:
            pipeline = RedisPipeline(client, namespace=self._redis_namespace)
            pipeline_store = cast(PipelineStore, self.store)
            old = run.resume_from_run_id
            if old and not await pipeline_store.get_state(f"redis-pipeline-run:{old}"):
                old = None  # This run predates Redis; no accepted Redis work exists.
            if old and await pipeline_store.get_state(f"redis-pipeline-drained:{old}"):
                old = None  # Terminal errors retry via normal incremental discovery.
            await pipeline.start(
                run.run_id,
                generation=generation,
                resume_from=old,
            )
            await pipeline_store.set_state(
                f"redis-pipeline-run:{run.run_id}", pipeline.root
            )
            token = current_pipeline.set(pipeline)
            try:
                await work()
                await pipeline.assert_drained()
                await pipeline_store.set_state(
                    f"redis-pipeline-drained:{run.run_id}", "1"
                )
                if run.errors:
                    raise RuntimeError(
                        "Scan has failed source work; inspect source progress"
                    )
            finally:
                current_pipeline.reset(token)
