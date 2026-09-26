"""Run-scoped durable work context inherited by the four source coroutines."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from pydantic import TypeAdapter

from jobfeed.adapters.queue.redis_pipeline import RedisPipeline
from jobfeed.domain.models import JobPosting

current_pipeline: ContextVar[RedisPipeline | None] = ContextVar(
    "scan_pipeline", default=None
)
POSTING = TypeAdapter(JobPosting)
POSTINGS = TypeAdapter(list[JobPosting])


async def durable_posting(
    name: str, payload: Any, work: Callable[[Any], Awaitable[JobPosting | None]]
) -> JobPosting | None:
    pipeline = current_pipeline.get()
    if pipeline is None:
        return await work(payload)

    async def execute(saved: Any) -> Any:
        result = await work(saved)
        return None if result is None else POSTING.dump_python(result, mode="json")

    result = await pipeline.step(name, payload, execute)
    return None if result is None else POSTING.validate_python(result)
