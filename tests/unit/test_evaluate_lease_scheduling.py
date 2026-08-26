"""Call-path tests for evaluate scheduling after run-lease ownership loss."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.models import (
    JobPosting,
    LLMRequest,
    LLMResponse,
    PipelineRun,
    StageAResult,
)
from jobfeed.ports.prompts import PromptBundle
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services._evaluate_stage_b import _score_stage_b
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)

_NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


class _LeaseProbe:
    def __init__(self) -> None:
        self.is_lost = False

    def lose(self) -> None:
        self.is_lost = True

    def ensure_active(self) -> None:
        if self.is_lost:
            raise RunLeaseLostError("test lease lost")


class _Budget:
    def __init__(self, lease: _LeaseProbe, *, lose_during_reserve: bool) -> None:
        self._lease = lease
        self._lose_during_reserve = lose_during_reserve

    async def reserve(self) -> str:
        if self._lose_during_reserve:
            self._lease.lose()
        return "2026-08-12"


class _CountingLLM:
    def __init__(self, *, stage_a_score: int = 10) -> None:
        self.calls = 0
        self._stage_a_score = stage_a_score

    async def complete(self, _request: object) -> LLMResponse:
        self.calls += 1
        content = (
            f'{{"score": {self._stage_a_score}, "one_line": "ok", '
            '"timing_eligible": "eligible"}'
        )
        return LLMResponse(
            content=content,
            model="mock",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            cached=False,
            latency_ms=1,
        )


class _Store:
    def __init__(self, lease: _LeaseProbe) -> None:
        self._lease = lease
        self.lose_during_save_stage_a = False
        self.lose_during_refresh_stage_b = False
        self.stage_b_skipped: list[str] = []

    async def claim_pending_stage_a(self, **_kwargs: object) -> list[JobPosting]:
        return []

    async def preview_claimable_stage_a(self, **_kwargs: object) -> list[JobPosting]:
        return []

    async def load_gate_candidates(self, **_kwargs: object) -> list[GateCandidate]:
        return []

    async def claim_stage_a_by_ids(
        self, _job_ids: list[str], **_kwargs: object
    ) -> list[JobPosting]:
        return []

    async def claim_pending_stage_b(self, **_kwargs: object) -> list[JobPosting]:
        return []

    async def release_stage_a_claim(self, _job_id: str) -> None:
        return None

    async def release_stage_b_claim(self, _job_id: str) -> None:
        return None

    async def refresh_stage_b_claim(self, _job_id: str) -> None:
        if self.lose_during_refresh_stage_b:
            self._lease.lose()

    async def save_stage_a(self, _job_id: str, _result: StageAResult) -> None:
        if self.lose_during_save_stage_a:
            self._lease.lose()

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        self.stage_b_skipped.append(job_id)


class _StoreOps:
    async def get_cost(self, _day: str) -> None:
        return None

    async def record_cost(self, **_kwargs: object) -> None:
        return None

    async def record_llm_usage_with_cost(self, **_kwargs: object) -> None:
        return None


class _Renderer:
    def render_stage_a(self, **_kwargs: object) -> PromptBundle:
        return _bundle()

    def render_stage_b(self, **_kwargs: object) -> PromptBundle:
        return _bundle()


class _Logger:
    def info(self, _event: str, **_kwargs: object) -> None:
        return None

    def warning(self, _event: str, **_kwargs: object) -> None:
        return None

    def error(self, _event: str, **_kwargs: object) -> None:
        return None

    def debug(self, _event: str, **_kwargs: object) -> None:
        return None


def _bundle() -> PromptBundle:
    return PromptBundle(messages=[], prompt_hash="prompt", resume_hash="resume")


def _job() -> JobPosting:
    return JobPosting(
        id="1",
        platform="test",
        canonical_id="lease-boundary",
        url="https://example.test/1",
        title="Software Engineer",
        company="Example",
        location="Remote",
        discovered_at=_NOW,
        jd_text="x" * 300,
    )


def _service(
    lease: _LeaseProbe,
) -> tuple[EvaluateService, _Store, _CountingLLM]:
    store = _Store(lease)
    llm = _CountingLLM()
    deps = EvaluateDependencies(
        store=store,  # type: ignore[arg-type]
        store_ops=_StoreOps(),  # type: ignore[arg-type]
        store_status=store,  # type: ignore[arg-type]
        prompt_renderer=_Renderer(),  # type: ignore[arg-type]
        llm_stage_a=llm,  # type: ignore[arg-type]
        llm_stage_b=llm,  # type: ignore[arg-type]
    )
    config = EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a="mock-a",
            stage_b="mock-b",
            max_concurrent=1,
            max_daily_score_calls=100,
            max_daily_cost_usd=100.0,
        ),
        stage_a_threshold=60,
        resume_text="resume",
    )
    return EvaluateService(deps=deps, config=config, logger=_Logger()), store, llm  # type: ignore[arg-type]


def _run() -> PipelineRun:
    return PipelineRun(run_id="run", started_at=_NOW, source="evaluate")


async def test_stage_a_lease_loss_during_budget_reserve_starts_no_llm() -> None:
    """Stage A rechecks ownership after reserving and before model scheduling."""
    lease = _LeaseProbe()
    service, _, llm = _service(lease)
    service._budget = _Budget(lease, lose_during_reserve=True)  # type: ignore[assignment]

    with pytest.raises(RunLeaseLostError):
        await service._call_parse_a(
            "1",
            LLMRequest(messages=[], model="mock-a"),
            _bundle(),
            _run(),
            lease,  # type: ignore[arg-type]
        )

    assert llm.calls == 0


async def test_stage_b_lease_loss_during_budget_reserve_starts_no_llm() -> None:
    """Stage B rechecks ownership after reserving and before model scheduling."""
    lease = _LeaseProbe()
    service, _, llm = _service(lease)
    service._budget = _Budget(lease, lose_during_reserve=True)  # type: ignore[assignment]

    with pytest.raises(RunLeaseLostError):
        await _score_stage_b(service, _job(), _run(), llm, lease)  # type: ignore[arg-type]

    assert llm.calls == 0


async def test_stage_b_lease_loss_entering_claim_maintenance_starts_no_llm() -> None:
    """Stage B rechecks ownership after the awaited claim refresh."""
    lease = _LeaseProbe()
    service, store, llm = _service(lease)
    service._budget = _Budget(lease, lose_during_reserve=False)  # type: ignore[assignment]
    store.lose_during_refresh_stage_b = True

    with pytest.raises(RunLeaseLostError):
        await _score_stage_b(service, _job(), _run(), llm, lease)  # type: ignore[arg-type]

    assert llm.calls == 0


async def test_lease_loss_during_stage_a_save_starts_no_stage_b_skip() -> None:
    """A completed Stage A write cannot authorize a later skip after lease loss."""
    lease = _LeaseProbe()
    service, store, _ = _service(lease)
    service._budget = _Budget(lease, lose_during_reserve=False)  # type: ignore[assignment]
    store.lose_during_save_stage_a = True

    with pytest.raises(RunLeaseLostError):
        await service._score_stage_a(_job(), _run(), lease)  # type: ignore[arg-type]

    assert store.stage_b_skipped == []
