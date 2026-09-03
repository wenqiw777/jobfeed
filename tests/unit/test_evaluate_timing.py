"""Tests for EvaluateService on_progress callback and ML-gate port usage."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from jobfeed.adapters.ml.mock import MockGate
from jobfeed.domain.errors import RunLeaseLostError
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
from jobfeed.services._evaluate_gate import gate_representatives, gate_unrated
from jobfeed.services._evaluate_stage_b import _run_stage_b
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)
from tests.support.run_leases import SuccessfulRunLeaseMixin

CLEAN_JD = (
    "Entry-level software engineering role on our platform team. Write Python "
    "and Go code, build backend services, and ship product features end to end. "
    "New grads and early-career engineers are welcome to apply. You will "
    "collaborate with senior engineers, review code, and learn our stack while "
    "contributing to reliable, well-tested services in production from day one."
)
RUN_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
SPLIT_GATE_FAIL_SCORE = 0.1


# ---------------------------------------------------------------------------
# Test doubles (minimal surface for these tests)
# ---------------------------------------------------------------------------


class FakeStore(SuccessfulRunLeaseMixin):
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

    async def update_pipeline_run_status(self, _run: object) -> None:
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


def _deps(store: FakeStore, *, gate: object | None = None) -> EvaluateDependencies:
    return EvaluateDependencies(
        store=store,  # type: ignore[arg-type]
        store_ops=StubStoreOps(),  # type: ignore[arg-type]
        store_status=store,  # type: ignore[arg-type]
        prompt_renderer=StubPromptRenderer(),  # type: ignore[arg-type]
        llm_stage_a=StageALLM(),  # type: ignore[arg-type]
        llm_stage_b=StageALLM(),  # type: ignore[arg-type]
        ml_gate=gate,  # type: ignore[arg-type]
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
async def test_stage_a_progress_exposes_real_queue_and_processed_count() -> None:
    """Live progress carries a truthful Stage A denominator and completion."""
    store = FakeStore([_job("a"), _job("b", title="Machine Learning Research Intern")])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    calls: list[PipelineRun] = []

    await service.run(
        stage="a",
        corpus="unrated",
        on_progress=lambda run: calls.append(replace(run)),
    )

    stage_a = [call for call in calls if call.progress_stage == "stage_a"]
    assert stage_a[0].stage_a_total == 2  # noqa: PLR2004
    assert [call.stage_a_processed for call in stage_a] == [0, 1, 2]
    assert calls[-2].progress_stage == "finalizing"


@pytest.mark.asyncio
async def test_stage_b_progress_exposes_claimed_queue_and_processed_count() -> None:
    """Live progress derives the Stage B denominator from the claimed batch."""
    first = _job("a")
    second = _job("b", title="Machine Learning Research Intern")

    class StageBStore(FakeStore):
        async def claim_pending_stage_b(self, **_kw: object) -> list[JobPosting]:
            return [first, second]

        async def get_stage_a_scores(self, _job_ids: list[str]) -> dict[str, int]:
            return {"a": 90, "b": 80}

        async def save_stage_b(self, _job_id: str, _result: object) -> None:
            pass

        async def save_stage_b_error(self, _job_id: str, _message: str) -> None:
            pass

    store = StageBStore([first, second])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    calls: list[PipelineRun] = []

    await service.run(
        stage="b",
        corpus="unrated",
        on_progress=lambda run: calls.append(replace(run)),
    )

    stage_b = [call for call in calls if call.progress_stage == "stage_b"]
    assert stage_b[1].stage_b_total == 2  # noqa: PLR2004
    assert [call.stage_b_processed for call in stage_b[1:]] == [0, 1, 2]


def test_progress_snapshot_keeps_completed_evaluation_count() -> None:
    """A later run failure must not reset already completed evaluation work."""
    store = FakeStore([])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    snapshots: list[PipelineRun] = []
    service._on_progress = lambda run: snapshots.append(replace(run))
    run = PipelineRun(
        run_id="run",
        started_at=RUN_AT,
        source="evaluate",
        stage_a_scored=1808,
        stage_b_scored=68,
    )

    service._emit_progress(run)

    assert snapshots[0].jobs_scored == 1876  # noqa: PLR2004


@pytest.mark.asyncio
async def test_stage_b_exception_drains_workers_and_releases_owned_claims() -> None:  # noqa: C901
    """Run finalization cannot race ahead of sibling workers after one escapes."""
    first = _job("a")
    second = _job("b")
    second_started = asyncio.Event()
    second_cancelled = asyncio.Event()

    class StageBStore(FakeStore):
        def __init__(self) -> None:
            super().__init__([first, second])
            self.released: list[str] = []

        async def claim_pending_stage_b(self, **_kw: object) -> list[JobPosting]:
            return [first, second]

        async def get_stage_a_scores(self, _job_ids: list[str]) -> dict[str, int]:
            return {"a": 90, "b": 80}

        async def release_stage_b_claim(self, job_id: str) -> None:
            self.released.append(job_id)

    class CoordinatedBudget:
        def __init__(self) -> None:
            self.calls = 0

        async def has_budget(self) -> bool:
            return True

        async def reserve(self) -> str:
            self.calls += 1
            if self.calls == 1:
                await second_started.wait()
                raise RuntimeError("database is locked")
            return "2026-09-02"

    class BlockingLLM(StageALLM):
        async def complete(self, _request: object) -> LLMResponse:
            second_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                second_cancelled.set()
                raise
            raise AssertionError("unreachable")

    class ActiveLease:
        lease_lost = False

        def ensure_active(self) -> None:
            return None

    store = StageBStore()
    deps = replace(_deps(store), llm_stage_b=BlockingLLM())
    service = EvaluateService(
        deps=deps,
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    service._budget = CoordinatedBudget()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="database is locked"):
        await _run_stage_b(
            service,
            PipelineRun(run_id="run", started_at=RUN_AT, source="evaluate"),
            10,
            None,
            ActiveLease(),  # type: ignore[arg-type]
        )

    assert second_cancelled.is_set()
    assert sorted(store.released) == ["a", "b"]


@pytest.mark.asyncio
async def test_stage_b_lease_loss_does_not_release_claims_from_stale_owner() -> None:
    """Cleanup must not mutate claims after the evaluation lease is lost."""
    job = _job("a")

    class StageBStore(FakeStore):
        def __init__(self) -> None:
            super().__init__([job])
            self.released: list[str] = []

        async def claim_pending_stage_b(self, **_kw: object) -> list[JobPosting]:
            return [job]

        async def get_stage_a_scores(self, _job_ids: list[str]) -> dict[str, int]:
            return {"a": 90}

        async def release_stage_b_claim(self, job_id: str) -> None:
            self.released.append(job_id)

    class LostLease:
        lease_lost = False

        def ensure_active(self) -> None:
            if self.lease_lost:
                raise RunLeaseLostError("test lease lost")

    class LosingBudget:
        def __init__(self, lease: LostLease) -> None:
            self.lease = lease

        async def has_budget(self) -> bool:
            return True

        async def reserve(self) -> str:
            self.lease.lease_lost = True
            return "2026-09-02"

    store = StageBStore()
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    lease = LostLease()
    service._budget = LosingBudget(lease)  # type: ignore[assignment]

    with pytest.raises(RunLeaseLostError, match="test lease lost"):
        await _run_stage_b(
            service,
            PipelineRun(run_id="run", started_at=RUN_AT, source="evaluate"),
            10,
            None,
            lease,  # type: ignore[arg-type]
        )

    assert store.released == []


@pytest.mark.asyncio
async def test_on_progress_fires_for_both_stages() -> None:
    """stage='both' streams each real phase and ends with final counters."""
    store = FakeStore([_job("a")])
    service = EvaluateService(
        deps=_deps(store),
        config=_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )
    calls: list[PipelineRun] = []
    await service.run(
        stage="both",
        corpus="unrated",
        on_progress=lambda run: calls.append(replace(run)),
    )

    phases = [call.progress_stage for call in calls]
    assert phases[0] == "preparing"
    assert "ml_gate" in phases
    assert "stage_a" in phases
    assert "stage_b" in phases
    assert calls[-2].progress_stage == "finalizing"
    assert calls[-1].finished_at is not None


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
# Gate port usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_unrated_awaits_the_port_directly() -> None:
    """gate_unrated awaits MLGate.predict_batch on the caller's loop.

    Threading is the adapter's concern (XGBoostGate offloads internally), so
    the service must not wrap the port call in to_thread/asyncio.run — a
    genuinely async gate implementation has to stay legal.
    """
    gate = MockGate()
    store = FakeStore([_job("a")])
    deps = _deps(store, gate=gate)
    run = PipelineRun(run_id="run-1", started_at=RUN_AT, source="evaluate")

    with patch.object(gate, "predict_batch", wraps=gate.predict_batch) as mock_predict:
        passed = await gate_unrated(deps, gate, run, [_job("a")], dry_run=False)

    mock_predict.assert_awaited_once()
    assert len(mock_predict.await_args.args[0]) == 1
    assert store.gate_results  # results persisted 1:1
    assert isinstance(passed, list)


class _SplitGate:
    """Gate double: passes jobs whose id is in the configured set."""

    def __init__(self, pass_ids: set[str]) -> None:
        self._pass_ids = pass_ids

    async def predict_batch(self, jobs: list[object]) -> list[MLGateResult]:
        """Return pass/fail per configured id set."""
        return [
            MLGateResult(
                score=0.9 if job.job_id in self._pass_ids else 0.1,  # type: ignore[attr-defined]
                result="pass" if job.job_id in self._pass_ids else "fail",  # type: ignore[attr-defined]
            )
            for job in jobs
        ]


class _ConcurrencyRecordingStore(FakeStore):
    """Record simultaneous gate-result writes without touching a database."""

    def __init__(self, candidates: list[JobPosting]) -> None:
        super().__init__(candidates)
        self.active_writes = 0
        self.peak_writes = 0

    async def save_ml_gate_result(self, job_id: str, result: MLGateResult) -> None:
        self.active_writes += 1
        self.peak_writes = max(self.peak_writes, self.active_writes)
        try:
            await asyncio.sleep(0.01)
            await super().save_ml_gate_result(job_id, result)
        finally:
            self.active_writes -= 1


@pytest.mark.asyncio
async def test_gate_result_persistence_is_bounded() -> None:
    """A large gate batch must not open one SQLite worker thread per row."""
    jobs = [_job(str(index)) for index in range(20)]
    store = _ConcurrencyRecordingStore(jobs)
    gate = _SplitGate(pass_ids={job.id or "" for job in jobs})
    deps = _deps(store, gate=gate)
    run = PipelineRun(run_id="run-1", started_at=RUN_AT, source="evaluate")

    await gate_unrated(
        deps,
        gate,
        run,
        jobs,
        dry_run=False,
        max_concurrent=3,
    )

    assert store.peak_writes == 3  # noqa: PLR2004 - configured cap
    assert len(store.gate_results) == len(jobs)


@pytest.mark.asyncio
async def test_gate_representatives_keeps_model_failures_nonblocking() -> None:
    """Low model scores are persisted for learning but still reach Quick.

    The scored counters cannot stand in for this: Stage A limit/budget cap
    them below the survivor count, which is exactly what the funnel's
    "after gate" stage must report.
    """
    jobs = [_job("a"), _job("b"), _job("c")]
    reps = [
        GateCandidate(job=jobs[0], ml_gate_result="pass"),  # prior-run pass
        GateCandidate(job=jobs[1], ml_gate_result=None),  # model pass
        GateCandidate(job=jobs[2], ml_gate_result=None),  # model fail
    ]
    gate = _SplitGate(pass_ids={jobs[1].id or ""})
    store = FakeStore(jobs)
    deps = _deps(store, gate=gate)
    run = PipelineRun(run_id="run-1", started_at=RUN_AT, source="evaluate")

    survivors = await gate_representatives(
        deps, _config(ml_gate_enabled=True), run, reps, dry_run=False
    )

    assert {j.id for j in survivors} == {jobs[0].id, jobs[1].id, jobs[2].id}
    assert run.jobs_gate_passed == 3  # noqa: PLR2004 - all candidates reach Quick
    assert run.jobs_ml_gated == 0
    persisted = dict(store.gate_results)
    assert persisted[jobs[2].id or ""].result == "pass"
    assert persisted[jobs[2].id or ""].score == SPLIT_GATE_FAIL_SCORE


@pytest.mark.asyncio
async def test_gate_progress_tracks_persisted_results_without_event_flood() -> None:
    """ML-gate progress exposes the real queue and reaches its denominator."""
    jobs = [_job(str(index), title=f"Software Engineer {index}") for index in range(60)]
    reps = [GateCandidate(job=job, ml_gate_result=None) for job in jobs]
    gate = _SplitGate(pass_ids={job.id or "" for job in jobs})
    store = FakeStore(jobs)
    run = PipelineRun(run_id="run-1", started_at=RUN_AT, source="evaluate")
    snapshots: list[PipelineRun] = []

    await gate_representatives(
        _deps(store, gate=gate),
        _config(ml_gate_enabled=True),
        run,
        reps,
        dry_run=False,
        on_progress=lambda: snapshots.append(replace(run)),
    )

    assert snapshots[0].ml_gate_total == 60  # noqa: PLR2004
    assert snapshots[0].ml_gate_processed == 0
    assert snapshots[-1].ml_gate_processed == 60  # noqa: PLR2004
    assert len(snapshots) <= 52  # noqa: PLR2004 - initial + 50 updates + final
