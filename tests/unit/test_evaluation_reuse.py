"""Exact, bounded run-local reuse without external providers."""
# ruff: noqa: PLR2004 -- exact fixture counts and byte budgets are assertions

import asyncio
from dataclasses import replace

import pytest

from jobfeed.domain.models import LLMRequest, Message, StageAResult
from jobfeed.services._evaluate_reuse import EvaluationReuse


def _request(text: str = "JD") -> LLMRequest:
    return LLMRequest(model="synthetic", messages=[Message(role="user", content=text)])


def _result() -> StageAResult:
    return StageAResult(
        score=80,
        one_line="Synthetic evidence",
        timing_eligible="eligible",
        model="synthetic",
        prompt_hash="existing",
        resume_hash="existing",
        cost_usd=0.1,
    )


async def test_exact_identity_stage_client_and_cost() -> None:
    cache = EvaluationReuse()
    client = object()
    async with cache.entry("a", client, _request()) as entry:
        assert entry.result is None
        entry.publish(_result(), "1")
    async with cache.entry("a", client, _request()) as entry:
        assert entry.result is not None and entry.result.cost_usd == 0
        assert entry.source_job_id == "1"
    for stage, other, request in [
        ("b", client, _request()),
        ("a", object(), _request()),
        ("a", client, _request("JD ")),
        ("a", client, replace(_request(), temperature=1)),
    ]:
        async with cache.entry(stage, other, request) as entry:
            assert entry.result is None


async def test_concurrent_waiter_only_sees_published_success() -> None:
    cache, client = EvaluationReuse(), object()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        async with cache.entry("a", client, _request()) as entry:
            entered.set()
            await finish.wait()
            entry.publish(_result(), "owner")

    async def waiter() -> str | None:
        async with cache.entry("a", client, _request()) as entry:
            return entry.source_job_id

    task = asyncio.create_task(owner())
    await entered.wait()
    waiting = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert not waiting.done()
    finish.set()
    await task
    assert await waiting == "owner"


async def test_failure_cancellation_and_limits() -> None:
    cache, client = EvaluationReuse(max_entries=1, max_bytes=10000), object()
    with pytest.raises(asyncio.CancelledError):
        async with cache.entry("a", client, _request()):
            raise asyncio.CancelledError
    async with cache.entry("a", client, _request()) as entry:
        assert entry.result is None
        entry.publish(_result(), "1")
    async with cache.entry("a", client, _request("other")) as entry:
        entry.publish(_result(), "2")
    async with cache.entry("a", client, _request()) as entry:
        assert entry.result is None
    assert cache.retained_bytes <= 10000
    tiny = EvaluationReuse(max_bytes=1)
    async with tiny.entry("a", client, _request()) as entry:
        entry.publish(_result(), "1")
    assert tiny.retained_bytes == 0


async def test_cancelled_waiter_does_not_cancel_owner() -> None:
    cache, client = EvaluationReuse(), object()
    async with cache.entry("a", client, _request()) as owner:

        async def wait() -> None:
            async with cache.entry("a", client, _request()):
                raise AssertionError("cancelled waiter must not enter")

        task = asyncio.create_task(wait())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.publish(_result(), "owner")
    async with cache.entry("a", client, _request()) as entry:
        assert entry.source_job_id == "owner"


async def test_byte_bound_evicts_old_inputs() -> None:
    cache, client = EvaluationReuse(max_bytes=10000), object()
    for index in range(20):
        async with cache.entry("a", client, _request(str(index) * 500)) as entry:
            entry.publish(_result(), str(index))
        assert cache.retained_bytes <= 10000
    async with cache.entry("a", client, _request("0" * 500)) as entry:
        assert entry.result is None
