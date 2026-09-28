"""Service boundaries for run-local reuse, using only synthetic completions."""
# ruff: noqa: PLR2004 -- exact provider call and result counts are assertions

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.llm.mock import MockLLM
from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.models import LLMRequest
from jobfeed.services._evaluate_stage_b import _score_stage_b
from tests.unit.test_evaluate_lease_scheduling import _job, _LeaseProbe, _run, _service


def _setup():
    lease = _LeaseProbe()
    service, store, llm = _service(lease)
    renderer = JinjaPromptRenderer(Path("src/jobfeed/templates"))
    service._deps = replace(service._deps, prompt_renderer=renderer)
    store.save_stage_a = AsyncMock()
    store.save_stage_b = AsyncMock()
    store.save_stage_a_error = AsyncMock()
    service._budget.reserve = AsyncMock(return_value="2026-08-12")
    service._deps.store_ops.record_llm_usage_with_cost = AsyncMock()
    return service, store, llm, lease


async def test_stage_a_identical_jobs_share_paid_call_and_own_results() -> None:
    service, store, llm, lease = _setup()
    run = _run()
    await asyncio.gather(
        *(
            service._score_stage_a(replace(_job(), id=str(i)), run, lease)
            for i in range(3)
        )
    )
    assert llm.calls == 1
    assert store.save_stage_a.await_count == 3
    assert service._budget.reserve.await_count == 1
    assert service._deps.store_ops.record_llm_usage_with_cost.await_count == 1
    assert store.save_stage_a.call_args_list[-1].args[1].cost_usd == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("location", "Seattle"),
        ("platform", "linkedin"),
        ("title", "Other Engineer"),
        ("company", "Other"),
        ("jd_text", "x" * 300 + " "),
    ],
)
async def test_changed_actual_input_does_not_reuse(field: str, value: str) -> None:
    service, _, llm, lease = _setup()
    await service._score_stage_a(_job(), _run(), lease)
    await service._score_stage_a(
        replace(_job(), id="2", **{field: value}), _run(), lease
    )
    assert llm.calls == 2


async def test_failure_before_save_never_publishes() -> None:
    service, store, llm, lease = _setup()
    store.save_stage_a.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        await service._score_stage_a(_job(), _run(), lease)
    store.save_stage_a.side_effect = None
    await service._score_stage_a(replace(_job(), id="2"), _run(), lease)
    assert llm.calls == 2


async def test_hit_survives_exhausted_budget_and_parse_failure_not_cached() -> None:
    service, store, llm, lease = _setup()
    good = await llm.complete(LLMRequest(messages=[], model="mock"))
    llm.complete = AsyncMock(side_effect=[replace(good, content="bad"), good])
    await service._score_stage_a(_job(), _run(), lease)
    assert llm.complete.await_count == 2
    service._budget.reserve.reset_mock()
    service._budget.reserve.return_value = None
    await service._score_stage_a(replace(_job(), id="2"), _run(), lease)
    assert llm.complete.await_count == 2
    service._budget.reserve.assert_not_awaited()
    assert store.save_stage_a.await_count == 2


async def test_stage_b_hit_no_usage_and_different_rough_score_misses() -> None:
    service, store, llm, lease = _setup()
    response = await MockLLM().complete(LLMRequest(messages=[], model="stage-b"))
    llm.complete = AsyncMock(return_value=replace(response, cost_usd=0.1))
    run = _run()
    for job_id, score in [("1", 80), ("2", 80), ("3", 70)]:
        assert (
            await _score_stage_b(
                service,
                replace(_job(), id=job_id),
                run,
                llm,
                lease,
                stage_a_score=score,
            )
            == "completed"
        )
    assert llm.complete.await_count == 2
    assert service._budget.reserve.await_count == 2
    assert store.save_stage_b.await_count == 3
    assert store.save_stage_b.call_args_list[1].args[1].cost_usd == 0


async def test_lost_lease_after_save_cannot_publish() -> None:
    service, store, llm, lease = _setup()
    store.save_stage_a.side_effect = lambda *_: lease.lose()
    with pytest.raises(RunLeaseLostError):
        await service._score_stage_a(_job(), _run(), lease)
    assert service._reuse.retained_bytes == 0
    store.save_stage_a.side_effect = None
    await service._score_stage_a(replace(_job(), id="2"), _run(), _LeaseProbe())
    assert llm.calls == 2


async def test_stage_b_waiter_cancel_keeps_owner_and_claim_maintenance() -> None:
    service, store, llm, lease = _setup()
    entered, finish, waiter_refreshed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    response = await MockLLM().complete(LLMRequest(messages=[], model="stage-b"))

    async def complete(_request):
        entered.set()
        await finish.wait()
        return response

    async def refresh(job_id):
        if job_id == "2":
            waiter_refreshed.set()

    llm.complete = AsyncMock(side_effect=complete)
    store.refresh_stage_b_claim = AsyncMock(side_effect=refresh)
    owner = asyncio.create_task(_score_stage_b(service, _job(), _run(), llm, lease))
    await entered.wait()
    waiter = asyncio.create_task(
        _score_stage_b(service, replace(_job(), id="2"), _run(), llm, lease)
    )
    await waiter_refreshed.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not owner.done()
    finish.set()
    assert await owner == "completed"
    assert (
        await _score_stage_b(service, replace(_job(), id="3"), _run(), llm, lease)
        == "completed"
    )
    assert llm.complete.await_count == 1
    assert store.save_stage_b.await_count == 2
    assert service._budget.reserve.await_count == 1


@pytest.mark.parametrize("fail", [False, True])
async def test_leased_run_clears_cache_on_success_and_failure(
    monkeypatch, fail: bool
) -> None:
    service, _, _, lease = _setup()
    lease.run = _run()
    monkeypatch.setattr("jobfeed.services.evaluate.run_auto_decay", AsyncMock())

    async def score(*_args) -> None:
        assert service._reuse.retained_bytes == 0
        await service._score_stage_a(_job(), lease.run, lease)
        assert service._reuse.retained_bytes > 0
        if fail:
            raise RuntimeError("synthetic failure")

    service._run_stage_a = score
    if fail:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            await service._run_leased(
                lease,
                stage="a",
                corpus="unrated",
                limit=10,
                max_days=None,
                job_ids=None,
                on_progress=None,
            )
    else:
        await service._run_leased(
            lease,
            stage="a",
            corpus="unrated",
            limit=10,
            max_days=None,
            job_ids=None,
            on_progress=None,
        )
    assert service._reuse.retained_bytes == 0
