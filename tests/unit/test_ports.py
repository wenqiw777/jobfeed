"""Unit tests for the Phase 0 port protocol contract."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from jobfeed.domain.models import (
    AutoDecayResult,
    JobEvaluation,
    JobPosting,
    LLMRequest,
    LLMResponse,
    MLGateResult,
    PipelineRun,
    QualityBand,
    SaveJobResult,
    StageAResult,
    StageBResult,
    StatusInfo,
)
from jobfeed.ports.llm import LLMClient
from jobfeed.ports.source import (
    DiscoverResult,
    EnrichResult,
    EnrichSession,
    SessionSource,
    SimpleSource,
)
from jobfeed.ports.store import JobStore
from tests.support.factories import fixed_time, make_job

MAX_STAGE_A_SCORE = 100
SESSION_DISCOVERY_DURATION_S = 1.5


class FakeStore:
    """Minimal structural implementation of the JobStore protocol."""

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Persist or update a job posting.

        Args:
            job: Job posting to save.

        Returns:
            Upsert result with the persisted identity.
        """
        return SaveJobResult(job_id=job.id or "1", inserted=True, updated=False)

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load a job by store identity.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Job posting when found.
        """
        job = make_job()
        job.id = job_id
        return job

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List recent jobs.

        Args:
            limit: Maximum jobs to return.

        Returns:
            Recent job postings.
        """
        return [make_job()][:limit]

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None:
        """Persist a successful Stage A result.

        Args:
            job_id: Store-assigned job identity.
            result: Stage A result to persist.
        """

    async def save_stage_a_error(self, job_id: str, error: str) -> None:
        """Persist a Stage A error.

        Args:
            job_id: Store-assigned job identity.
            error: Error message to persist.
        """

    async def save_stage_b(self, job_id: str, result: StageBResult) -> None:
        """Persist a successful Stage B result.

        Args:
            job_id: Store-assigned job identity.
            result: Stage B result to persist.
        """

    async def save_stage_b_error(self, job_id: str, error: str) -> None:
        """Persist a Stage B error.

        Args:
            job_id: Store-assigned job identity.
            error: Error message to persist.
        """

    async def load_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load jobs pending Stage A.

        Args:
            limit: Maximum jobs to return.
            quality_bands: Filter by jd_quality.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter.

        Returns:
            Job postings pending Stage A.
        """
        return [make_job()][:limit]

    async def load_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load Stage A-completed, Stage B-pending jobs.

        Args:
            limit: Maximum jobs to return.
            max_days: Freshness filter.

        Returns:
            Job postings pending Stage B.
        """
        return [make_job()][:limit]

    async def list_evaluated_jobs(self, limit: int = 100) -> list[JobEvaluation]:
        """List evaluated jobs for digest rendering.

        Args:
            limit: Maximum evaluations to return.

        Returns:
            Job evaluations.
        """
        return [JobEvaluation(job=make_job(), stage_a=None, stage_b=None)][:limit]

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Persist pipeline run counters.

        Args:
            run: Pipeline run to persist.
        """

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load a pipeline run by identity.

        Args:
            run_id: Pipeline run identity.

        Returns:
            Pipeline run when found.
        """
        return PipelineRun(run_id=run_id, started_at=fixed_time(), source="scan")

    async def job_exists(
        self,
        *,
        platform: str,
        canonical_id: str,
    ) -> bool:
        """Check job existence by natural key.

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.

        Returns:
            True if exists.
        """
        return False

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        """Mark Stage B as skipped.

        Args:
            job_id: Store-assigned job identity.
        """

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Fetch evaluation for a job.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Evaluation if found.
        """
        return None

    async def top_evaluated_jobs(
        self,
        *,
        min_score: int = 0,
        limit: int = 100,
    ) -> list[JobEvaluation]:
        """Stage B-completed jobs by score.

        Args:
            min_score: Score threshold.
            limit: Max results.

        Returns:
            Sorted evaluations.
        """
        return []

    async def save_ml_gate_result(
        self,
        job_id: str,
        result: MLGateResult,
    ) -> None:
        """Persist ML gate decision.

        Args:
            job_id: Store-assigned job identity.
            result: Gate decision.
        """

    async def transition_status(
        self,
        *,
        job_id: str,
        new_status: str,
        reason: str | None = None,
        resume_variant: str | None = None,
        force: bool = False,
        i_mean_it: bool = False,
        followup_grace_days: int = 7,
    ) -> str:
        """Transition job status.

        Args:
            job_id: Store-assigned job identity.
            new_status: Target status.
            reason: Optional reason.
            resume_variant: Optional variant.
            force: Bypass graph.
            i_mean_it: Double-gate for archived → new.
            followup_grace_days: Days until follow-up.

        Returns:
            New status string.
        """
        return new_status

    async def get_status(self, job_id: str) -> StatusInfo | None:
        """Get current status.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Status info if found.
        """
        return None

    async def restore_from_archived(self, job_id: str) -> str:
        """Restore archived job.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Restored status.
        """
        return "scored"

    async def auto_decay(
        self,
        *,
        ghost_days: int = 30,
        archive_ignored_days: int = 14,
    ) -> AutoDecayResult:
        """Sweep stale jobs.

        Args:
            ghost_days: Ghost threshold.
            archive_ignored_days: Archive threshold.

        Returns:
            Decay counts.
        """
        return AutoDecayResult(ghosted=0, archived=0)

    async def connect(self) -> None:
        """Open backing store resources."""

    async def close(self) -> None:
        """Close backing store resources."""


class FakeLLM:
    """Minimal structural implementation of the LLMClient protocol."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a deterministic completion response.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Adapter-neutral completion response.
        """
        return LLMResponse(
            content="{}",
            model=request.model,
            input_tokens=1,
            output_tokens=1,
        )


class FakeEnrichSession:
    """Minimal structural implementation of the EnrichSession protocol."""

    async def enrich(self, posting: JobPosting) -> EnrichResult:
        """Return enrichment details for a posting.

        Args:
            posting: Posting to enrich.

        Returns:
            Enrichment result with JD text and quality.
        """
        return EnrichResult(
            jd_text=f"Detailed JD for {posting.title}",
            quality=QualityBand.GOOD,
            enrich_source="fake",
        )


class FakeSimpleSource:
    """Minimal structural implementation of the SimpleSource protocol."""

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:
        """Fetch jobs in one source call.

        Args:
            config: Source-specific configuration.

        Returns:
            Fetched job postings.
        """
        return [make_job()][: int(config.get("count", 1))]


class FakeSessionSource:
    """Minimal structural implementation of the SessionSource protocol."""

    async def discover(self, config: dict[str, object]) -> DiscoverResult:
        """Discover jobs that may need later enrichment.

        Args:
            config: Source-specific configuration.

        Returns:
            Discovery result for the source session.
        """
        duration_s = float(config.get("duration", 0.0))
        return DiscoverResult(postings=[make_job()], duration_s=duration_s)

    async def enrich_session(self) -> AbstractAsyncContextManager[EnrichSession]:
        """Open an enrichment session context manager.

        Returns:
            Async context manager yielding an enrichment session.
        """

        @asynccontextmanager
        async def manager() -> AsyncIterator[FakeEnrichSession]:
            yield FakeEnrichSession()

        return manager()


def test_job_store_protocol_has_required_async_methods() -> None:
    """JobStore should expose the full async persistence contract."""
    required_methods = [
        "save_job",
        "get_job",
        "list_jobs",
        "job_exists",
        "save_stage_a",
        "save_stage_a_error",
        "save_stage_b",
        "save_stage_b_error",
        "mark_stage_b_skipped",
        "load_pending_stage_a",
        "load_pending_stage_b",
        "list_evaluated_jobs",
        "get_evaluation",
        "top_evaluated_jobs",
        "save_ml_gate_result",
        "transition_status",
        "get_status",
        "restore_from_archived",
        "auto_decay",
        "record_pipeline_run",
        "get_pipeline_run",
        "connect",
        "close",
    ]

    assert isinstance(FakeStore(), JobStore)
    for method_name in required_methods:
        assert inspect.iscoroutinefunction(getattr(JobStore, method_name))


@pytest.mark.asyncio
async def test_llm_client_protocol_completes_requests() -> None:
    """LLMClient should complete adapter-neutral requests."""
    request = LLMRequest(messages=[], model="mock/stage-a")

    assert isinstance(FakeLLM(), LLMClient)
    response = await FakeLLM().complete(request)

    assert response.model == "mock/stage-a"
    assert response.content == "{}"


@pytest.mark.asyncio
async def test_source_protocols_cover_simple_and_session_sources() -> None:
    """Source protocols should cover single-call and session-based scraping."""
    simple_source = FakeSimpleSource()
    session_source = FakeSessionSource()

    assert isinstance(simple_source, SimpleSource)
    assert isinstance(session_source, SessionSource)
    assert isinstance(FakeEnrichSession(), EnrichSession)
    assert await simple_source.fetch_jobs({"count": 1}) == [make_job()]

    discovered = await session_source.discover(
        {"duration": SESSION_DISCOVERY_DURATION_S},
    )
    manager = await session_source.enrich_session()

    async with manager as session:
        enriched = await session.enrich(discovered.postings[0])

    assert discovered.needs_reauth is False
    assert discovered.error is None
    assert discovered.duration_s == SESSION_DISCOVERY_DURATION_S
    assert enriched.quality is QualityBand.GOOD
    assert enriched.error is None
    assert enriched.posted_at is None
