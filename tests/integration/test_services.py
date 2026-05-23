"""Integration tests for scan, evaluate, and digest services."""

from __future__ import annotations

import asyncio
import json

import pytest

from jobfeed.adapters.llm.mock import MockLLM
from jobfeed.adapters.sources.mock import MockSource
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.config import LLMSettings, ScoringSettings, Settings
from jobfeed.domain.models import LLMRequest, LLMResponse, StageAResult
from jobfeed.observability import configure_logging, get_logger
from jobfeed.services.digest import DigestService
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.scan import ScanService

pytestmark = pytest.mark.postgres

MOCK_COUNT = 3
SINGLE_SOURCE_COUNT = 2
EXPECTED_ERROR_COUNT = 1
STAGE_COUNT_MULTIPLIER = 2
MAX_CONCURRENT_FOR_TEST = 2


class RecordingLogger:
    """Small in-memory logger for service tests that do not inspect structlog."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> object:
        """Record an info event.

        Args:
            event: Event name.
            kwargs: Event attributes.

        Returns:
            Recorded event tuple.
        """
        item = (event, kwargs)
        self.events.append(item)
        return item

    def error(self, event: str, **kwargs: object) -> object:
        """Record an error event.

        Args:
            event: Event name.
            kwargs: Event attributes.

        Returns:
            Recorded event tuple.
        """
        item = (event, kwargs)
        self.events.append(item)
        return item

    def warning(self, event: str, **kwargs: object) -> object:
        """Record a warning event.

        Args:
            event: Event name.
            kwargs: Event attributes.

        Returns:
            Recorded event tuple.
        """
        item = (event, kwargs)
        self.events.append(item)
        return item

    def debug(self, event: str, **kwargs: object) -> object:
        """Record a debug event.

        Args:
            event: Event name.
            kwargs: Event attributes.

        Returns:
            Recorded event tuple.
        """
        item = (event, kwargs)
        self.events.append(item)
        return item


class FailingSource:
    """Source test double that always fails."""

    async def fetch_jobs(self, _config: dict[str, object]) -> list[object]:
        """Fail one source fetch.

        Args:
            _config: Ignored source config.

        Raises:
            RuntimeError: Always raised to exercise per-source isolation.
        """
        raise RuntimeError("source failed")


class BadStageALLM:
    """LLM test double that returns unparsable Stage A output."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return invalid JSON for any request.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Invalid completion response.
        """
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
        """Count a completion call and return a valid mock-style response.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Valid Stage A response.
        """
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
        """Track active calls and return a valid Stage A response.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Valid Stage A response.
        """
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


class SlowLLM:
    """LLM test double that exceeds a small timeout."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Sleep longer than the test timeout.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Valid response when not cancelled.
        """
        await asyncio.sleep(1)
        return LLMResponse(
            content='{"score": 75, "one_line": "ok", "timing_eligible": "eligible"}',
            model=request.model,
            input_tokens=0,
            output_tokens=0,
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
    service = EvaluateService(store, MockLLM(), Settings(), RecordingLogger())

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

    Args:
        store: Connected PostgresStore.
    """
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": MOCK_COUNT})]
    )
    service = EvaluateService(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
        RecordingLogger(),
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

    Args:
        store: Connected PostgresStore.
    """
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": MOCK_COUNT})]
    )
    service = EvaluateService(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=70)),
        RecordingLogger(),
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

    Args:
        store: Connected PostgresStore.
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

    service = EvaluateService(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
        RecordingLogger(),
    )
    run = await service.run()

    assert run.stage_b_scored == 0
    assert await store.load_pending_stage_b() == []


async def test_evaluate_dry_run_excludes_below_threshold_stage_b(
    store: PostgresStore,
) -> None:
    """dry-run excludes below-threshold Stage B jobs and mutates nothing.

    Args:
        store: Connected PostgresStore.
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

    logger = RecordingLogger()
    service = EvaluateService(
        store,
        CountingLLM(),
        Settings(scoring=ScoringSettings(stage_a_threshold=80)),
        logger,
    )
    await service.run(dry_run=True)

    # Dry run writes nothing: the below-threshold row stays pending (not skipped).
    assert len(await store.load_pending_stage_b()) == 1
    # And it is not reported as a Stage B dry-run candidate.
    stage_b_logged = [
        kwargs.get("job_id")
        for event, kwargs in logger.events
        if event == "evaluate_dry_run_job" and kwargs.get("stage") == "stage_b"
    ]
    assert job.id not in stage_b_logged


async def test_evaluate_service_persists_parse_failures_and_continues(
    store: PostgresStore,
) -> None:
    """Parse failures should persist explicit errors and increment run errors."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    service = EvaluateService(store, BadStageALLM(), Settings(), RecordingLogger())

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
    service = EvaluateService(
        store,
        llm,
        Settings(
            llm=LLMSettings(max_concurrent=MAX_CONCURRENT_FOR_TEST),
            scoring=ScoringSettings(stage_a_threshold=100),
        ),
        RecordingLogger(),
    )

    run = await service.run()

    assert run.stage_a_scored == MOCK_COUNT
    assert run.stage_b_scored == 0
    assert llm.max_active == MAX_CONCURRENT_FOR_TEST


async def test_evaluate_service_persists_llm_timeout_and_continues(
    store: PostgresStore,
) -> None:
    """LLM timeout failures should persist explicit stage errors."""
    await ScanService(store, RecordingLogger()).run(
        [("mock", MockSource(), {"count": SINGLE_SOURCE_COUNT})]
    )
    service = EvaluateService(
        store,
        SlowLLM(),
        Settings(llm=LLMSettings(timeout_s=0.001)),
        RecordingLogger(),
    )

    run = await service.run()
    pending = await store.load_pending_stage_a()
    evaluations = await store.list_evaluated_jobs()

    assert run.errors == SINGLE_SOURCE_COUNT
    # Stage A errors are retryable: the default "unrated" corpus includes
    # errored rows (plan Task 1), so a later run re-attempts them.
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
    service = EvaluateService(store, llm, Settings(), RecordingLogger())

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
    service = EvaluateService(store, llm, Settings(), logger)

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
    await EvaluateService(store, MockLLM(), Settings(), RecordingLogger()).run()

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
