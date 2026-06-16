"""Tests for EvaluateService on_progress callback and to_thread ML gate."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from jobfeed.adapters.ml.mock import MockGate
from jobfeed.domain.models import (
    AutoDecayResult,
    JobPosting,
    LLMResponse,
    MLGateResult,
    PipelineRun,
    StageAResult,
)
from jobfeed.ports.prompts import PromptBundle
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services._evaluate_gate import gate_unrated
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)

CLEAN_JD = (
    "Entry-level software engineering role on our platform team. Write Python "
    "and Go code, build backend services, and ship product features end to end. "
    "New grads and early-career engineers are welcome to apply. You will "
    "collaborate with senior engineers, review code, and learn our stack while "
    "contributing to reliable, well-tested services in production from day one."
)
RUN_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Test doubles (minimal surface for these tests)
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory store covering the funnel + Stage A claim/score surface."""

    def __init__(self, candidates: list[JobPosting]) -> None:
        self._candidates = candidates
        self.gate_results: list[tuple[str, MLGateResult]] = []

    async def load_gate_candidates(self, **_kw: object) -> list[GateCandidate]:
        return [GateCandidate(job=j, ml_gate_result=None) for j in self._candidates]

    async def claim_stage_a_by_ids(
        self, job_ids: list[str], **_kw: object
    ) -> list[JobPosting]:
        wanted = set(job_ids)
        return [j for j in self._candidates if j.id in wanted]

    async def claim_pending_stage_a(self, **_kw: object) -> list[JobPosting]:
        return []

    async def preview_claimable_stage_a(self, **_kw: object) -> list[JobPosting]:
        return list(self._candidates)

    async def claim_pending_stage_b(self, **_kw: object) -> list[JobPosting]:
        return []

    async def load_pending_stage_b(self, **_kw: object) -> list[JobPosting]:
        return []

    async def release_stage_a_claim(self, _job_id: str) -> None:
        pass

    async def release_stage_b_claim(self, _job_id: str) -> None:
        pass

    async def refresh_stage_b_claim(self, _job_id: str) -> None:
        pass

    async def save_ml_gate_result(self, job_id: str, result: MLGateResult) -> None:
        self.gate_results.append((job_id, result))

    async def save_stage_a(self, _job_id: str, _result: StageAResult) -> None:
        pass

    async def save_stage_a_error(self, _job_id: str, _message: str) -> None:
        pass

    async def mark_stage_b_skipped(self, _job_id: str) -> None:
        pass

    async def record_pipeline_run(self, _run: PipelineRun) -> None:
        pass

    async def auto_decay(self, **_kw: object) -> AutoDecayResult:
        return AutoDecayResult(ghosted=0, archived=0)

    async def record_step_timing(self, _timing: object) -> None:
        pass

    async def record_step_timings(self, _timings: object) -> None:
        pass


class StubStoreOps:
    async def get_cost(self, _day: str) -> None:
        return None

    async def record_cost(self, **_kw: object) -> None:
        pass

    async def record_llm_usage_with_cost(self, **_kw: object) -> None:
        pass


class StubPromptRenderer:
    def render_stage_a(self, **_kw: object) -> PromptBundle:
        return PromptBundle(messages=[], prompt_hash="ph", resume_hash="rh")

    def render_stage_b(self, **_kw: object) -> PromptBundle:
        return PromptBundle(messages=[], prompt_hash="ph", resume_hash="rh")


class StageALLM:
    async def complete(self, _request: object) -> LLMResponse:
        content = '{"score": 80, "one_line": "ok", "timing_eligible": "eligible"}'
        return LLMResponse(
            content=content,
            model="mock",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            cached=False,
            latency_ms=1,
        )


class RecordingLogger:
    def info(self, _event: str, **_kw: object) -> None:
        pass

    def warning(self, _event: str, **_kw: object) -> None:
        pass

    def error(self, _event: str, **_kw: object) -> None:
        pass

    def debug(self, _event: str, **_kw: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _job(job_id: str, *, title: str = "Software Engineer") -> JobPosting:
    return JobPosting(
        id=job_id,
        platform="greenhouse",
        canonical_id=f"can-{job_id}",
        url=f"https://example.com/{job_id}",
        title=title,
        company="Acme",
        location="Remote",
        discovered_at=RUN_AT,
        jd_text=CLEAN_JD,
        jd_quality="full",
        posted_at=RUN_AT,
    )


def _deps(store: FakeStore, *, gate: MockGate | None = None) -> EvaluateDependencies:
    return EvaluateDependencies(
        store=store,  # type: ignore[arg-type]
        store_ops=StubStoreOps(),  # type: ignore[arg-type]
        store_status=store,  # type: ignore[arg-type]
        prompt_renderer=StubPromptRenderer(),  # type: ignore[arg-type]
        llm_stage_a=StageALLM(),  # type: ignore[arg-type]
        llm_stage_b=StageALLM(),  # type: ignore[arg-type]
        ml_gate=gate,
    )


def _config(*, ml_gate_enabled: bool = False) -> EvaluateRuntimeConfig:
    return EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a="mock-a",
            stage_b="mock-b",
            max_concurrent=4,
            max_daily_score_calls=1000,
            max_daily_cost_usd=100.0,
        ),
        stage_a_threshold=60,
        resume_text="resume",
        ml_gate_enabled=ml_gate_enabled,
    )


# ---------------------------------------------------------------------------
# on_progress tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_progress_fires_per_stage() -> None:
    """on_progress is called after funnel+stage_a, and a final time at end."""
    store = FakeStore([_job("a")])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    calls: list[PipelineRun] = []
    await service.run(stage="a", corpus="unrated", on_progress=calls.append)
    # After stage_a completes and the final notify.
    assert len(calls) >= 2  # noqa: PLR2004
    assert calls[-1].finished_at is not None


@pytest.mark.asyncio
async def test_on_progress_fires_for_both_stages() -> None:
    """stage='both' fires after stage_a, after stage_b, and at end."""
    store = FakeStore([_job("a")])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    calls: list[PipelineRun] = []
    await service.run(stage="both", corpus="unrated", on_progress=calls.append)
    # 3 calls: after stage_a, after stage_b, and final.
    assert len(calls) == 3  # noqa: PLR2004


@pytest.mark.asyncio
async def test_on_progress_none_does_not_error() -> None:
    """on_progress=None (the default) runs without error."""
    store = FakeStore([_job("a")])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    run = await service.run(stage="a", corpus="unrated", on_progress=None)
    assert run.finished_at is not None


# ---------------------------------------------------------------------------
# to_thread test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_unrated_uses_to_thread() -> None:
    """gate_unrated offloads predict_batch to a thread via asyncio.to_thread."""
    gate = MockGate()
    store = FakeStore([_job("a")])
    deps = _deps(store, gate=gate)
    run = PipelineRun(run_id="run-1", started_at=RUN_AT, source="evaluate")

    with patch(
        "jobfeed.services._evaluate_gate.asyncio.to_thread",
        wraps=asyncio.to_thread,
    ) as mock_to_thread:
        await gate_unrated(deps, gate, run, [_job("a")], dry_run=False)
        assert mock_to_thread.called
        # The first positional arg should be _predict_sync.
        call_args = mock_to_thread.call_args
        assert call_args is not None
