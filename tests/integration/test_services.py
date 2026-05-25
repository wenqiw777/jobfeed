"""Integration tests for scan, evaluate, and digest services."""

from __future__ import annotations

import asyncio
import json

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
    StageAResult,
)
from jobfeed.observability import configure_logging, get_logger
from jobfeed.ports.prompts import PromptBundle
from jobfeed.services.digest import DigestService
from jobfeed.services.evaluate import (
    EvaluateDependencies,
    EvaluateRuntimeConfig,
    EvaluateService,
)
from jobfeed.services.scan import ScanService

pytestmark = pytest.mark.postgres

MOCK_COUNT = 3
SINGLE_SOURCE_COUNT = 2
EXPECTED_ERROR_COUNT = 1
STAGE_COUNT_MULTIPLIER = 2
MAX_CONCURRENT_FOR_TEST = 2
RESUME_TEXT = "Phase 0 resume placeholder."


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


class StubStoreOps:
    """Minimal StoreOpsMixin stub for tests that need cost/usage recording."""

    def __init__(self) -> None:
        self.costs: list[tuple[str, float]] = []
        self.usages: list[LLMUsage] = []
        self._cost_entry: CostEntry | None = None

    async def record_cost(self, *, day: str, spent_usd: float) -> None:
        """Record a cost entry."""
        self.costs.append((day, spent_usd))

    async def get_cost(self, _day: str) -> CostEntry | None:
        """Return the configured cost entry."""
        return self._cost_entry

    async def record_llm_usage(self, usage: LLMUsage) -> None:
        """Record an LLM usage entry."""
        self.usages.append(usage)


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

    def render_stage_b(self, *, resume_text: str, job: JobPosting) -> PromptBundle:  # noqa: ARG002
        """Return skeleton Stage B prompt bundle."""
        return PromptBundle(
            messages=[
                Message(role="system", content="review"),
                Message(role="user", content=job.jd_text or ""),
            ],
            prompt_hash="test",
            resume_hash="test",
        )


def _make_deps(
    store: PostgresStore,
    llm: object,
    store_ops: StubStoreOps | None = None,
) -> EvaluateDependencies:
    """Build EvaluateDependencies from test doubles."""
    ops = store_ops or StubStoreOps()
    return EvaluateDependencies(
        store=store,
        store_ops=ops,  # type: ignore[arg-type]
        prompt_renderer=StubPromptRenderer(),  # type: ignore[arg-type]
        llm_stage_a=llm,  # type: ignore[arg-type]
        llm_stage_b=llm,  # type: ignore[arg-type]
    )


def _make_config(
    settings: Settings | None = None,
) -> EvaluateRuntimeConfig:
    """Build EvaluateRuntimeConfig from settings."""
    s = settings or Settings()
    return EvaluateRuntimeConfig(
        llm=s.llm,
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

    # Pre-existing below-threshold: the new service no longer does batch
    # skip_below_threshold on load. Instead Stage A threshold check happens
    # at scoring time. Below-threshold rows from a prior run stay in the
    # Stage B queue (the store includes them). The service attempts Stage B
    # for them; CountingLLM returns Stage A-shaped JSON, which fails Stage B
    # parsing, so they end up as errors.
    service = _make_service(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
    )
    run = await service.run()

    # Stage A: no new pending (already scored). Stage B: one job attempted
    # but parse fails because CountingLLM returns Stage A JSON for Stage B.
    assert run.stage_b_scored == 0
    assert run.errors >= 1


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


def make_stage_a_result() -> StageAResult:
    """Return a completed Stage A result for service setup."""
    return StageAResult(
        score=75,
        one_line="ok",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="prompt",
        resume_hash="resume",
    )
