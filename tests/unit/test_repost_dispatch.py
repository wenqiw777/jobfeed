"""A fresh repost observation blocks dispatch even after a job was queued."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from jobfeed.domain.models import PipelineRun
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services._evaluate_gate import gate_representatives
from jobfeed.services._evaluate_stage_b import _score_stage_b
from jobfeed.services.evaluate import EvaluateService
from tests.support.sqlite_jobs_evaluations import FIXED_NOW, make_job


@pytest.mark.parametrize("stage", ["a", "b"])
async def test_fresh_repost_stops_queued_model_dispatch(stage):
    job = replace(make_job("queued"), id="1")
    store = SimpleNamespace(
        get_job=AsyncMock(return_value=replace(job, is_repost=True))
    )
    llm = SimpleNamespace(complete=AsyncMock())
    service = SimpleNamespace(
        _deps=SimpleNamespace(store=store, llm_stage_a=llm, prompt_renderer=Mock()),
        _budget=Mock(),
        _logger=Mock(),
    )
    lease = Mock()
    run = PipelineRun(source="evaluate", run_id="test", started_at=FIXED_NOW)
    if stage == "a":
        await EvaluateService._score_stage_a(service, job, run, lease)
    else:
        assert await _score_stage_b(service, job, run, llm, lease) == "skipped"
    llm.complete.assert_not_awaited()
    service._budget.reserve.assert_not_called()
    service._deps.prompt_renderer.render_stage_a.assert_not_called()
    service._deps.prompt_renderer.render_stage_b.assert_not_called()


async def test_gate_skips_repost_even_when_received_from_legacy_store():
    job = replace(make_job("repost"), id="1", is_repost=True)
    gate = SimpleNamespace(predict_batch=AsyncMock())
    deps = SimpleNamespace(
        store=SimpleNamespace(get_job=AsyncMock(return_value=job)), ml_gate=gate
    )
    config = SimpleNamespace(llm=SimpleNamespace(max_concurrent=1))
    survivors = await gate_representatives(
        deps,
        config,
        PipelineRun(source="evaluate", run_id="test", started_at=FIXED_NOW),
        [GateCandidate(job=job, ml_gate_result=None)],
        False,
        mode="filter",
    )
    assert survivors == []
    gate.predict_batch.assert_not_awaited()


async def test_repost_observed_during_budget_reservation_stops_call():
    job = replace(make_job("race"), id="1")
    store = SimpleNamespace(
        get_job=AsyncMock(side_effect=[job, replace(job, is_repost=True)])
    )
    llm = SimpleNamespace(complete=AsyncMock())
    service = SimpleNamespace(
        _deps=SimpleNamespace(store=store, llm_stage_a=llm),
        _budget=SimpleNamespace(reserve=AsyncMock(return_value="2026-09-20")),
    )
    result = await EvaluateService._call_parse_a(
        service,
        "1",
        Mock(),
        Mock(),
        PipelineRun(source="evaluate", run_id="race", started_at=FIXED_NOW),
        Mock(),
    )
    assert result is None
    llm.complete.assert_not_awaited()
