"""Integration tests for scan, evaluate, and digest services."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.llm.mock import MockLLM
from jobfeed.adapters.sources.mock import MockSource
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.config import LLMSettings, ScoringSettings, Settings
from jobfeed.domain.models import (
    CostEntry,
    JobPosting,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    Message,
    MLGateResult,
    StageAResult,
)
from jobfeed.observability import configure_logging, get_logger
from jobfeed.ports.prompts import PromptBundle
from jobfeed.services import _evaluate_claims as claim_helpers
from jobfeed.services.digest import DigestService
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)
from jobfeed.services.scan import ScanService

pytestmark = pytest.mark.postgres

MOCK_COUNT = 3
SINGLE_SOURCE_COUNT = 2
EXPECTED_ERROR_COUNT = 1
STAGE_COUNT_MULTIPLIER = 2
MAX_CONCURRENT_FOR_TEST = 2
STAGE_RETRY_CALLS = 2
LLM_USAGE_STAGE_COUNT = 2
MIN_HEARTBEAT_REFRESH_CALLS = 2
RESUME_TEXT = "Phase 0 resume placeholder."
TEST_LLM_SETTINGS = LLMSettings(stage_a="mock/stage-a", stage_b="mock/stage-b")


class RecordingLogger:
    """Small in-memory logger for service tests that do not inspect structlog."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> object:
        """Record an info event."""
        item = (event, kwargs)
        self.events.append(item)
        return item

    def error(self, event: str, **kwargs: object) -> object:
        """Record an error event."""
        item = (event, kwargs)
        self.events.append(item)
        return item

    def warning(self, event: str, **kwargs: object) -> object:
        """Record a warning event."""
        item = (event, kwargs)
        self.events.append(item)
        return item

    def debug(self, event: str, **kwargs: object) -> object:
        """Record a debug event."""
        item = (event, kwargs)
        self.events.append(item)
        return item


class FailingSource:
    """Source test double that always fails."""

    async def fetch_jobs(self, _config: dict[str, object]) -> list[object]:
        """Fail one source fetch."""
        raise RuntimeError("source failed")


class BadStageALLM:
    """LLM test double that returns unparsable Stage A output."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return invalid JSON for any request."""
        return LLMResponse(
            content="not json",
            model=request.model,
            input_tokens=0,
            output_tokens=0,
        )


class CostlyBadStageALLM:
    """LLM test double that returns invalid Stage A output with usage cost."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return invalid JSON and non-zero usage cost."""
        self.calls += 1
        return LLMResponse(
            content="not json",
            model=request.model,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.25,
        )


class CostlyBadStageBLLM:
    """LLM test double that returns invalid Stage B output with usage cost."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return invalid JSON and non-zero usage cost."""
        self.calls += 1
        return LLMResponse(
            content="not json",
            model=request.model,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.25,
        )


class AgingStageBErrorLLM:
    """Stage B LLM double that ages the claim before failing."""

    def __init__(self, store: PostgresStore, job_id: str) -> None:
        self._store = store
        self._job_id = job_id

    async def complete(self, _request: LLMRequest) -> LLMResponse:
        """Age the active claim and raise a runtime failure."""
        await _age_evaluation_claim(self._store, self._job_id)
        raise RuntimeError("primary stage_b failed")


class ReclaimProbeSweepLLM(MockLLM):
    """Sweep LLM double that checks whether another runner can reclaim."""

    def __init__(self, store: PostgresStore, threshold: int) -> None:
        self._store = store
        self._threshold = threshold
        self.reclaimed_ids: list[str | None] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Probe Stage B reclaimability, then return a valid sweep result."""
        reclaimed = await self._store.claim_pending_stage_b(
            limit=1,
            stage_a_threshold=self._threshold,
        )
        self.reclaimed_ids = [job.id for job in reclaimed]
        return await super().complete(request)


class YieldingMockLLM(MockLLM):
    """Mock LLM that yields so lease-heartbeat tests can run background work."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Yield control before returning the deterministic mock response."""
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await super().complete(request)


class CountingLLM:
    """LLM test double that records whether it was called."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Count a completion call and return a valid mock-style response."""
        self.calls += 1
        return LLMResponse(
            content='{"score": 75, "one_line": "ok", "timing_eligible": "eligible"}',
            model=request.model,
            input_tokens=0,
            output_tokens=0,
        )


class RecordingMockLLM(MockLLM):
    """Mock LLM that records requests for prompt assertions."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Record the request and return the deterministic mock response."""
        self.requests.append(request)
        return await super().complete(request)


class TrackingLLM:
    """LLM test double that records maximum concurrent calls."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Track active calls and return a valid Stage A response."""
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            return LLMResponse(
                content=(
                    '{"score": 75, "one_line": "ok", "timing_eligible": "eligible"}'
                ),
                model=request.model,
                input_tokens=0,
                output_tokens=0,
            )
        finally:
            self.active -= 1


class ErrorLLM:
    """LLM test double that raises a runtime error."""

    async def complete(self, _request: LLMRequest) -> LLMResponse:
        """Always raise a runtime error."""
        raise RuntimeError("LLM adapter error")


class CountingErrorLLM:
    """LLM test double that counts calls and raises a runtime error."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, _request: LLMRequest) -> LLMResponse:
        """Count and raise a runtime error."""
        self.calls += 1
        raise RuntimeError("LLM adapter error")


class FlakyStageALLM:
    """LLM test double that fails once, then returns valid Stage A JSON."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Raise once, then return a valid Stage A response."""
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient LLM adapter error")
        return LLMResponse(
            content='{"score": 75, "one_line": "ok", "timing_eligible": "eligible"}',
            model=request.model,
            input_tokens=0,
            output_tokens=0,
        )


class StubStoreOps:
    """Minimal StoreOpsMixin stub for tests that need cost/usage recording."""

    def __init__(self) -> None:
        self.costs: list[tuple[str, float, int]] = []
        self.usages: list[LLMUsage] = []
        self._cost_entry: CostEntry | None = None

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Record a cost entry."""
        self.costs.append((day, spent_usd, calls))

    async def get_cost(self, _day: str) -> CostEntry | None:
        """Return the configured cost entry."""
        return self._cost_entry

    async def record_llm_usage(self, usage: LLMUsage) -> None:
        """Record an LLM usage entry."""
        self.usages.append(usage)

    async def record_llm_usage_with_cost(
        self,
        *,
        day: str,
        spent_usd: float,
        usage: LLMUsage,
    ) -> None:
        """Record a cost and LLM usage entry."""
        self.costs.append((day, spent_usd, 0))
        self.usages.append(usage)


class DynamicBudgetStoreOps(StubStoreOps):
    """StoreOps stub whose budget reads reflect prior recorded costs."""

    async def get_cost(self, day: str) -> CostEntry | None:
        """Return cost totals accumulated by this test stub."""
        if not self.costs:
            return None
        return CostEntry(
            day=day,
            spent_usd=sum(spent for _, spent, _ in self.costs),
            calls=sum(calls for _, _, calls in self.costs),
            last_updated=datetime.now(UTC),
        )


class StubPromptRenderer:
    """Minimal PromptRenderer for integration tests."""

    def render_stage_a(self, *, resume_text: str, job: JobPosting) -> PromptBundle:  # noqa: ARG002
        """Return skeleton Stage A prompt bundle."""
        return PromptBundle(
            messages=[
                Message(role="system", content="score"),
                Message(role="user", content=job.jd_text or ""),
            ],
            prompt_hash="test",
            resume_hash="test",
        )

    def render_stage_b(
        self,
        *,
        resume_text: str,
        job: JobPosting,
        stage_a_score: int | None = None,
    ) -> PromptBundle:
        """Return skeleton Stage B prompt bundle."""
        del resume_text
        score_text = "" if stage_a_score is None else f"stage_a={stage_a_score}"
        return PromptBundle(
            messages=[
                Message(role="system", content="review"),
                Message(role="user", content=f"{job.jd_text or ''} {score_text}"),
            ],
            prompt_hash="test",
            resume_hash="test",
        )


def _make_deps(
    store: PostgresStore,
    llm: object,
    store_ops: StubStoreOps | None = None,
    sweep: object | None = None,
) -> EvaluateDependencies:
    """Build EvaluateDependencies from test doubles."""
    ops = store_ops or StubStoreOps()
    return EvaluateDependencies(
        store=store,
        store_ops=ops,  # type: ignore[arg-type]
        prompt_renderer=StubPromptRenderer(),  # type: ignore[arg-type]
        llm_stage_a=llm,  # type: ignore[arg-type]
        llm_stage_b=llm,  # type: ignore[arg-type]
        llm_stage_b_sweep=sweep,  # type: ignore[arg-type]
    )


def _make_config(
    settings: Settings | None = None,
) -> EvaluateRuntimeConfig:
    """Build EvaluateRuntimeConfig from settings."""
    s = settings or Settings(llm=TEST_LLM_SETTINGS)
    return EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a=s.llm.stage_a,
            stage_b=s.llm.stage_b,
            max_concurrent=s.llm.max_concurrent,
            max_daily_score_calls=s.llm.max_daily_score_calls,
            max_daily_cost_usd=s.llm.max_daily_cost_usd,
        ),
        stage_a_threshold=s.scoring.stage_a_threshold,
        resume_text=RESUME_TEXT,
    )


def _make_service(
    store: PostgresStore,
    llm: object,
    settings: Settings | None = None,
    logger: object | None = None,
    store_ops: StubStoreOps | None = None,
) -> EvaluateService:
    """Build EvaluateService from test doubles."""
    return EvaluateService(
        deps=_make_deps(store, llm, store_ops),
        config=_make_config(settings),
        logger=logger or RecordingLogger(),  # type: ignore[arg-type]
    )


async def test_evaluate_service_rejects_invalid_stage_before_llm(
    store: PostgresStore,
) -> None:
    """Invalid stage values should fail before claim or LLM work."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    llm = CountingLLM()
    service = _make_service(store, llm)

    with pytest.raises(ValueError, match="unknown evaluate stage"):
        await service.run(stage="typo")

    assert llm.calls == 0
    assert len(await store.load_pending_stage_a()) == 1


async def test_postgres_claim_stage_a_excludes_already_claimed(
    store: PostgresStore,
) -> None:
    """A claimed Stage A row should not be claimed by a second runner."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )

    first = await store.claim_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        limit=1,
    )
    second = await store.claim_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        limit=1,
    )

    assert len(first) == 1
    assert second == []
    assert first[0].id is not None
    await _age_evaluation_claim(store, first[0].id)
    stale = await store.claim_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        limit=1,
    )
    assert [job.id for job in stale] == [first[0].id]
    await store.release_stage_a_claim(first[0].id)
    reclaimed = await store.claim_pending_stage_a(
        quality_bands=frozenset({"full", "good"}),
        limit=1,
    )
    assert [job.id for job in reclaimed] == [first[0].id]


async def test_evaluate_dry_run_previews_gated_unscored_via_funnel(
    store: PostgresStore,
) -> None:
    """Dry-run previews the funnel survivor set (Decision 14), not the legacy
    claimable preview.

    Phase 5 makes the funnel (``load_gate_candidates`` -> hard-filter -> dedupe ->
    gate) the sole Stage-A candidate source for both real and dry runs, retiring
    ``claim_pending_stage_a``/``preview_claimable_stage_a`` from the evaluate path
    (Rev3 pt 4). A row that has a gate decision written but is not yet
    Stage-A-scored (gate-then-crash) stays an unrated funnel candidate and so is
    previewed — the Decision 8 crash-recovery case. (A row stranded
    ``in_progress`` past the claim TTL by an interrupted *scoring* run is now ALSO
    re-surfaced under "unrated" via the gate-candidates stale-takeover predicate;
    covered by ``test_phase5_evaluate_chain`` /
    ``test_gate_candidates_query``.)
    """
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    # Gate-then-crash: a gate decision exists but no Stage A row yet.
    await store.save_ml_gate_result(job.id, make_pass_gate_result())

    run = await _make_service(store, CountingLLM()).run(stage="a", dry_run=True)

    assert [item.job_id for item in run.dry_run_preview] == [job.id]


async def test_postgres_claim_stage_b_excludes_already_claimed(
    store: PostgresStore,
) -> None:
    """A claimed Stage B row should not be claimed by a second runner."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result(score=90))

    first = await store.claim_pending_stage_b(limit=1, stage_a_threshold=80)
    second = await store.claim_pending_stage_b(limit=1, stage_a_threshold=80)

    assert [claimed.id for claimed in first] == [job.id]
    assert second == []
    await _age_evaluation_claim(store, job.id)
    stale = await store.claim_pending_stage_b(limit=1, stage_a_threshold=80)
    assert [claimed.id for claimed in stale] == [job.id]
    await store.release_stage_b_claim(job.id)
    reclaimed = await store.claim_pending_stage_b(limit=1, stage_a_threshold=80)
    assert [claimed.id for claimed in reclaimed] == [job.id]


async def test_evaluate_dry_run_previews_stale_stage_b_claim(
    store: PostgresStore,
) -> None:
    """Dry-run should show stale Stage B claims a real run would reclaim."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result(score=90))
    await store.claim_pending_stage_b(limit=1, stage_a_threshold=80)
    await _age_evaluation_claim(store, job.id)

    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )
    run = await service.run(stage="b", dry_run=True)

    assert [item.job_id for item in run.dry_run_preview] == [job.id]


async def test_stage_b_threshold_sync_skips_stale_claim_below_raised_threshold(
    store: PostgresStore,
) -> None:
    """A stale claim below a raised threshold should not stay in progress."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result(score=75))
    claimed = await store.claim_pending_stage_b(limit=1, stage_a_threshold=70)
    assert [item.id for item in claimed] == [job.id]
    await _age_evaluation_claim(store, job.id)
    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )

    run = await service.run(stage="b")

    status, error = await _get_stage_b_state(store, job.id)
    assert status == "skipped_below_threshold"
    assert error is None
    assert run.stage_b_scored == 0
    assert run.errors == 0


async def test_scan_service_saves_mock_jobs_and_single_source_name(
    store: PostgresStore,
) -> None:
    """ScanService should save mock jobs and set single-source run source."""
    service = ScanService(store, RecordingLogger())

    run = await service.run([("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})])
    jobs = await store.list_jobs()

    assert run.source == "mock"
    assert run.jobs_discovered == SINGLE_SOURCE_COUNT
    assert run.jobs_inserted == SINGLE_SOURCE_COUNT
    assert len(jobs) == SINGLE_SOURCE_COUNT


async def test_scan_service_uses_aggregate_source_for_multiple_sources(
    store: PostgresStore,
) -> None:
    """Multi-source scans should use aggregate pipeline source name."""
    service = ScanService(store, RecordingLogger())

    run = await service.run(
        [
            ("mock-a", MockSource(), {"count": SINGLE_SOURCE_COUNT}),
            ("mock-b", MockSource(), {"count": SINGLE_SOURCE_COUNT}),
        ]
    )

    assert run.source == "scan"
    assert run.jobs_discovered == SINGLE_SOURCE_COUNT * STAGE_COUNT_MULTIPLIER


async def test_scan_service_tracks_idempotent_updates(store: PostgresStore) -> None:
    """Repeated scans should count existing rows as updated."""
    service = ScanService(store, RecordingLogger())
    await service.run([("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})])

    second = await service.run([("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})])

    assert second.jobs_inserted == 0
    assert second.jobs_updated == SINGLE_SOURCE_COUNT


async def test_scan_service_continues_on_source_failure(store: PostgresStore) -> None:
    """One failing source should not stop other sources from being saved."""
    service = ScanService(store, RecordingLogger())

    run = await service.run(
        [
            ("bad", FailingSource(), {}),
            ("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT}),
        ]
    )

    assert run.errors == EXPECTED_ERROR_COUNT
    assert run.jobs_discovered == SINGLE_SOURCE_COUNT


async def test_evaluate_service_scores_stage_a_and_stage_b(
    store: PostgresStore,
) -> None:
    """EvaluateService should score pending jobs through both stages."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": MOCK_COUNT})]
    )
    service = _make_service(store, MockLLM())

    run = await service.run()
    evaluations = await store.list_evaluated_jobs()

    assert run.source == "evaluate"
    assert run.stage_a_scored == MOCK_COUNT
    assert run.stage_b_scored == MOCK_COUNT
    assert run.jobs_scored == MOCK_COUNT * STAGE_COUNT_MULTIPLIER
    assert all(item.stage_a is not None for item in evaluations)
    assert all(item.stage_b is not None for item in evaluations)


async def test_evaluate_service_skips_stage_b_below_threshold(
    store: PostgresStore,
) -> None:
    """Stage A scores below the threshold are gated out of Stage B.

    CountingLLM scores Stage A at 75; with a threshold of 80 every job is
    marked skipped, so Stage B is never called (no error) and its queue drains.
    """
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": MOCK_COUNT})]
    )
    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )

    run = await service.run()
    pending_b = await store.load_pending_stage_b()

    assert run.stage_a_scored == MOCK_COUNT
    assert run.stage_b_scored == 0
    assert run.errors == 0
    assert pending_b == []


async def test_evaluate_service_sends_stage_b_at_or_above_threshold(
    store: PostgresStore,
) -> None:
    """Stage A scores at or above the threshold proceed to Stage B (not gated).

    Score 75 >= threshold 70, so jobs are not skipped and Stage B is attempted;
    here it errors on the Stage-A-shaped payload, proving the gate let them
    through rather than skipping them.
    """
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": MOCK_COUNT})]
    )
    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=70)),
    )

    run = await service.run()

    assert run.stage_a_scored == MOCK_COUNT
    assert run.errors == MOCK_COUNT
    assert run.stage_b_scored == 0


async def test_evaluate_service_skips_preexisting_below_threshold_stage_b(
    store: PostgresStore,
) -> None:
    """Pre-existing below-threshold Stage A rows are gated out of Stage B.

    Simulates a row scored below threshold without the in-run skip path (legacy
    import / pre-gate scoring): it sits in the Stage B queue until the service
    marks it skipped.
    """
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    low = StageAResult(
        score=40,
        one_line="weak",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    await store.save_stage_a(job.id, low)
    assert len(await store.load_pending_stage_b()) == 1

    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )
    run = await service.run()
    pending_b = await store.load_pending_stage_b()

    assert pending_b == []
    assert run.stage_b_scored == 0
    assert run.errors == 0


async def test_evaluate_service_stage_b_limit_applies_after_threshold_filter(
    store: PostgresStore,
) -> None:
    """Stage B should fill the limit from threshold-eligible jobs."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 2})]
    )
    jobs = await store.list_jobs()
    low_job = jobs[0]
    high_job = jobs[1]
    assert low_job.id is not None
    assert high_job.id is not None
    await store.save_stage_a(
        low_job.id,
        StageAResult(
            score=40,
            one_line="weak",
            timing_eligible="eligible",
            model="mock/stage-a",
            prompt_hash="h",
            resume_hash="h",
            cost_usd=0.0,
        ),
    )
    await store.save_stage_a(
        high_job.id,
        StageAResult(
            score=90,
            one_line="strong",
            timing_eligible="eligible",
            model="mock/stage-a",
            prompt_hash="h",
            resume_hash="h",
            cost_usd=0.0,
        ),
    )

    service = _make_service(
        store,
        MockLLM(),
        Settings(llm=TEST_LLM_SETTINGS, scoring=ScoringSettings(stage_a_threshold=80)),
    )
    run = await service.run(stage="b", limit=1)

    assert run.stage_b_scored == 1
    assert run.errors == 0
    assert await store.load_pending_stage_b(stage_a_threshold=80) == []


async def test_evaluate_service_passes_stage_a_score_to_stage_b_prompt(
    store: PostgresStore,
) -> None:
    """Stage B prompt rendering should receive the stored Stage A score."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(
        job.id,
        StageAResult(
            score=88,
            one_line="strong",
            timing_eligible="eligible",
            model="mock/stage-a",
            prompt_hash="h",
            resume_hash="h",
            cost_usd=0.0,
        ),
    )
    llm = RecordingMockLLM()
    service = _make_service(store, llm)

    run = await service.run(stage="b")

    assert run.stage_b_scored == 1
    assert "stage_a=88" in llm.requests[0].messages[1].content


async def test_evaluate_service_stage_b_limit_zero_does_not_mark_skips(
    store: PostgresStore,
) -> None:
    """Stage B limit=0 should leave the Stage B queue untouched."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    low = StageAResult(
        score=40,
        one_line="weak",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    await store.save_stage_a(job.id, low)

    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )
    run = await service.run(stage="b", limit=0)

    assert run.stage_b_scored == 0
    assert run.errors == 0
    assert len(await store.load_pending_stage_b()) == 1


async def test_evaluate_dry_run_does_not_mutate_store(
    store: PostgresStore,
) -> None:
    """dry-run should not mutate the store and should log pending jobs."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    low = StageAResult(
        score=40,
        one_line="weak",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    await store.save_stage_a(job.id, low)
    assert len(await store.load_pending_stage_b()) == 1

    logger = RecordingLogger()
    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
        logger,
    )
    await service.run(dry_run=True)

    # Dry run writes nothing: the below-threshold row stays pending.
    assert len(await store.load_pending_stage_b()) == 1


async def test_stage_a_rescore_reopens_threshold_skipped_stage_b(
    store: PostgresStore,
) -> None:
    """A later above-threshold Stage A score should re-enter Stage B."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    low = StageAResult(
        score=40,
        one_line="weak",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    high = StageAResult(
        score=90,
        one_line="strong",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )

    await store.save_stage_a(job.id, low)
    await store.mark_stage_b_skipped(job.id)
    assert await store.load_pending_stage_b(stage_a_threshold=80) == []

    await store.save_stage_a(job.id, high)

    pending_ids = {item.id for item in await store.load_pending_stage_b()}
    assert job.id in pending_ids


async def test_stage_b_lower_threshold_reopens_previous_skips(
    store: PostgresStore,
) -> None:
    """Lowering the active threshold should requeue previously skipped jobs."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    low = StageAResult(
        score=75,
        one_line="ok",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    await store.save_stage_a(job.id, low)
    high_threshold = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )
    await high_threshold.run(stage="b")

    lowered = _make_service(
        store,
        MockLLM(),
        Settings(llm=TEST_LLM_SETTINGS, scoring=ScoringSettings(stage_a_threshold=70)),
    )
    run = await lowered.run(stage="b")

    assert run.stage_b_scored == 1
    assert run.errors == 0


async def test_stage_b_dry_run_previews_lowered_threshold_without_mutating(
    store: PostgresStore,
) -> None:
    """Dry-run should show rows a real lowered-threshold run would reopen."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    score = StageAResult(
        score=75,
        one_line="ok",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    await store.save_stage_a(job.id, score)
    high_threshold = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )
    await high_threshold.run(stage="b")
    assert await store.load_pending_stage_b(stage_a_threshold=70) == []

    logger = RecordingLogger()
    dry_run = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=70)),
        logger,
    )
    run = await dry_run.run(stage="b", dry_run=True)

    assert run.stage_b_scored == 0
    assert [event[1]["job_id"] for event in logger.events] == [job.id]
    assert await store.load_pending_stage_b(stage_a_threshold=70) == []


async def test_evaluate_service_stage_b_respects_budget_gate(
    store: PostgresStore,
) -> None:
    """Stage B should not call the LLM after the daily budget is exhausted."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    high = StageAResult(
        score=90,
        one_line="strong",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="h",
        resume_hash="h",
        cost_usd=0.0,
    )
    await store.save_stage_a(job.id, high)
    llm = CountingLLM()
    ops = StubStoreOps()
    ops._cost_entry = CostEntry(
        day="2026-05-25",
        spent_usd=1.0,
        calls=150,
        last_updated=datetime.now(UTC),
    )
    service = _make_service(store, llm, store_ops=ops)

    run = await service.run(stage="b")

    assert llm.calls == 0
    assert run.stage_b_scored == 0
    assert run.errors == 0
    assert len(await store.load_pending_stage_b(stage_a_threshold=80)) == 1


async def test_evaluate_service_retries_stage_a_runtime_failure(
    store: PostgresStore,
) -> None:
    """Stage A transient runtime failures should retry once before erroring."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    llm = FlakyStageALLM()
    service = _make_service(store, llm)

    run = await service.run(stage="a")

    assert llm.calls == STAGE_RETRY_CALLS
    assert run.stage_a_scored == 1
    assert run.errors == 0


async def test_evaluate_service_records_real_cost_and_usage_chain(
    store: PostgresStore,
) -> None:
    """Evaluate should write cost_ledger and llm_usage through the real store."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    service = _make_service(store, MockLLM(), store_ops=store)  # type: ignore[arg-type]

    run = await service.run()

    cost = await store.get_cost(datetime.now(UTC).strftime("%Y-%m-%d"))
    assert cost is not None
    assert cost.calls == LLM_USAGE_STAGE_COUNT
    pool = store._get_pool()
    rows = await pool.fetch(
        """SELECT job_id, stage, run_id
           FROM llm_usage
           WHERE run_id = $1
           ORDER BY stage""",
        run.run_id,
    )
    assert len(rows) == LLM_USAGE_STAGE_COUNT
    assert {row["stage"] for row in rows} == {"a", "b"}
    assert all(row["job_id"] is not None for row in rows)


async def test_evaluate_service_persists_parse_failures_and_continues(
    store: PostgresStore,
) -> None:
    """Parse failures should persist explicit errors and increment run errors."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    service = _make_service(store, BadStageALLM())

    run = await service.run()
    pending = await store.load_pending_stage_a()
    evaluations = await store.list_evaluated_jobs()

    assert run.errors == SINGLE_SOURCE_COUNT
    # Stage A errors are retryable: the default "unrated" corpus includes
    # errored rows (plan Task 1), so a later run re-attempts them.
    assert len(pending) == SINGLE_SOURCE_COUNT
    assert all(item.stage_a is None for item in evaluations)


async def test_evaluate_service_counts_parse_failure_cost_in_run_total(
    store: PostgresStore,
) -> None:
    """Parse-failure retries should still count successful LLM response cost."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    llm = CostlyBadStageALLM()
    service = _make_service(store, llm)

    run = await service.run(stage="a")

    assert llm.calls == STAGE_RETRY_CALLS
    assert run.errors == 1
    assert run.total_llm_cost_usd == pytest.approx(0.50)


async def test_evaluate_service_checks_budget_before_parse_retry(
    store: PostgresStore,
) -> None:
    """A costly parse failure should not retry after exhausting call budget."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    llm = CostlyBadStageALLM()
    service = _make_service(
        store,
        llm,
        Settings(llm=LLMSettings(max_daily_score_calls=1)),
        store_ops=DynamicBudgetStoreOps(),
    )

    run = await service.run(stage="a")

    assert llm.calls == 1
    assert run.errors == 0
    assert run.total_llm_cost_usd == pytest.approx(0.25)
    assert len(await store.load_pending_stage_a()) == 1


async def test_evaluate_service_counts_runtime_failures_against_budget(
    store: PostgresStore,
) -> None:
    """Runtime failures should consume the daily call budget before retry."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    llm = CountingErrorLLM()
    service = _make_service(
        store,
        llm,
        Settings(llm=LLMSettings(max_daily_score_calls=1)),
        store_ops=DynamicBudgetStoreOps(),
    )

    run = await service.run(stage="a")

    assert llm.calls == 1
    assert run.stage_a_scored == 0
    assert len(await store.load_pending_stage_a()) == 1


async def test_stage_b_sweep_parse_failure_uses_single_attempt(
    store: PostgresStore,
) -> None:
    """Stage B sweep should not retry parser failures again."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result())
    sweep = CostlyBadStageBLLM()
    service = EvaluateService(
        deps=_make_deps(store, ErrorLLM(), sweep=sweep),
        config=_make_config(),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )

    run = await service.run(stage="b")

    assert sweep.calls == 1
    assert run.stage_b_scored == 0
    assert run.errors == 1


async def test_stage_b_sweep_budget_exhaustion_persists_primary_failure(
    store: PostgresStore,
) -> None:
    """Budget-blocked sweep should not hide an already-failed primary call."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result(score=90))
    sweep = CountingLLM()
    service = EvaluateService(
        deps=_make_deps(
            store,
            ErrorLLM(),
            store_ops=DynamicBudgetStoreOps(),
            sweep=sweep,
        ),
        config=_make_config(
            Settings(
                llm=LLMSettings(
                    stage_a="mock/stage-a",
                    stage_b="mock/stage-b",
                    max_daily_score_calls=1,
                )
            )
        ),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )

    run = await service.run(stage="b")

    status, error = await _get_stage_b_state(store, job.id)
    assert sweep.calls == 0
    assert status == "error"
    assert error == "stage_b_sweep_failed"
    assert run.stage_b_scored == 0
    assert run.errors == 1


async def test_stage_b_sweep_refreshes_stale_claim_before_call(
    store: PostgresStore,
) -> None:
    """Sweep should refresh stale Stage B claims before unbounded LLM calls."""
    threshold = 70
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result(score=90))
    sweep = ReclaimProbeSweepLLM(store, threshold)
    service = EvaluateService(
        deps=_make_deps(store, AgingStageBErrorLLM(store, job.id), sweep=sweep),
        config=_make_config(
            Settings(
                llm=TEST_LLM_SETTINGS,
                scoring=ScoringSettings(stage_a_threshold=threshold),
            )
        ),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )

    run = await service.run(stage="b")

    assert sweep.reclaimed_ids == []
    assert run.stage_b_scored == 1
    assert run.errors == 0


async def test_stage_b_heartbeat_failure_does_not_mask_completed_response(
    store: PostgresStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed background refresh should not discard a returned LLM response."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": 1})]
    )
    job = (await store.list_jobs())[0]
    assert job.id is not None
    await store.save_stage_a(job.id, make_stage_a_result(score=90))
    monkeypatch.setattr(claim_helpers, "STAGE_B_LEASE_HEARTBEAT_SECONDS", 0.0)
    refresh_calls = 0

    async def flaky_refresh(job_id: str) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls > 1:
            raise RuntimeError("refresh failed")
        await PostgresStore.refresh_stage_b_claim(store, job_id)

    monkeypatch.setattr(store, "refresh_stage_b_claim", flaky_refresh)
    service = _make_service(
        store,
        YieldingMockLLM(),
        Settings(llm=TEST_LLM_SETTINGS, scoring=ScoringSettings(stage_a_threshold=80)),
    )

    run = await service.run(stage="b")

    status, error = await _get_stage_b_state(store, job.id)
    assert refresh_calls >= MIN_HEARTBEAT_REFRESH_CALLS
    assert status == "completed"
    assert error is None
    assert run.stage_b_scored == 1
    assert run.errors == 0


async def test_evaluate_service_respects_max_concurrent(
    store: PostgresStore,
) -> None:
    """Evaluation should use the configured LLM concurrency cap."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": MOCK_COUNT})]
    )
    llm = TrackingLLM()
    service = _make_service(
        store,
        llm,
        Settings(
            llm=LLMSettings(max_concurrent=MAX_CONCURRENT_FOR_TEST),
            scoring=ScoringSettings(stage_a_threshold=100),
        ),
    )

    run = await service.run()

    assert run.stage_a_scored == MOCK_COUNT
    assert run.stage_b_scored == 0
    assert llm.max_active == MAX_CONCURRENT_FOR_TEST


async def test_evaluate_service_persists_runtime_error_and_continues(
    store: PostgresStore,
) -> None:
    """LLM adapter errors should persist stage errors and increment run errors."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    service = _make_service(store, ErrorLLM())

    run = await service.run()
    pending = await store.load_pending_stage_a()
    evaluations = await store.list_evaluated_jobs()

    assert run.errors == SINGLE_SOURCE_COUNT
    assert len(pending) == SINGLE_SOURCE_COUNT
    assert all(item.stage_a is None for item in evaluations)


async def test_evaluate_service_dry_run_does_not_call_llm(
    store: PostgresStore,
) -> None:
    """dry_run should log pending jobs without calling the LLM."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    llm = CountingLLM()
    service = _make_service(store, llm)

    run = await service.run(dry_run=True)

    assert llm.calls == 0
    assert run.stage_a_scored == 0
    assert await store.get_pipeline_run(run.run_id) is None
    assert await store.list_evaluated_jobs() == []


async def test_evaluate_service_dry_run_logs_stage_b_pending_jobs(
    store: PostgresStore,
) -> None:
    """dry_run should include jobs already eligible for Stage B."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    jobs = await store.list_jobs()
    assert jobs[0].id is not None
    await store.save_stage_a(jobs[0].id, make_stage_a_result())
    llm = CountingLLM()
    logger = RecordingLogger()
    service = _make_service(store, llm, logger=logger)

    await service.run(dry_run=True)

    assert llm.calls == 0
    assert ("evaluate_dry_run_job", {"stage": "stage_b"}) in [
        (event, {"stage": fields["stage"]})
        for event, fields in logger.events
        if event == "evaluate_dry_run_job"
    ]


async def test_digest_service_returns_markdown_with_mock_job_data(
    store: PostgresStore,
) -> None:
    """DigestService should render Markdown from real stored evaluations."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    await _make_service(store, MockLLM()).run()

    digest = await DigestService(store, RecordingLogger()).run()

    assert "# Daily Digest" in digest
    assert "Backend Platform Intern" in digest
    assert "Mock evaluation" in digest


async def test_structlog_entries_include_run_id(
    store: PostgresStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Service logs emitted through structlog should include bound run_id."""
    configure_logging("info", "json")
    service = ScanService(store, get_logger())

    run = await service.run([("mock", MockSource(), {"count": 1})])

    output = capsys.readouterr().out.strip().splitlines()
    first_event = json.loads(output[0])
    assert first_event["run_id"] == run.run_id


def make_stage_a_result(score: int = 75) -> StageAResult:
    """Return a completed Stage A result for service setup."""
    return StageAResult(
        score=score,
        one_line="ok",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="prompt",
        resume_hash="resume",
    )


def make_pass_gate_result() -> MLGateResult:
    """Return a minimal passing ML gate decision for funnel setup."""
    return MLGateResult(score=1.0, result="pass", version="mock")


async def _age_evaluation_claim(store: PostgresStore, job_id: str) -> None:
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE evaluations SET updated_at = $2 WHERE job_id = $1",
            int(job_id),
            datetime.now(UTC) - timedelta(hours=2),
        )


async def _get_stage_b_state(
    store: PostgresStore,
    job_id: str,
) -> tuple[str | None, str | None]:
    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT stage_b_status, stage_b_error
               FROM evaluations
               WHERE job_id = $1""",
            int(job_id),
        )
    assert row is not None
    return row["stage_b_status"], row["stage_b_error"]
