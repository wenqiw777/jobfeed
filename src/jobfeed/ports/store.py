"""Persistence port protocols for Jobfeed domain objects."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    JobEvaluation,
    JobPosting,
    MLGateResult,
    PipelineRun,
    SaveJobResult,
    StageAResult,
    StageBResult,
)


@runtime_checkable
class JobStore(Protocol):
    """Core persistence capability for the Jobfeed pipeline.

    Jobs, staged evaluation, ML-gate results, and pipeline runs. Optional
    capabilities (ops, claims, batch eval, status/workflow, applications)
    live in their own port mixins.
    """

    async def connect(self) -> None:
        """Open backing store resources."""
        ...

    async def close(self) -> None:
        """Close backing store resources."""
        ...

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Persist or upsert a job by (platform, canonical_id).

        Args:
            job: Job posting to persist.

        Returns:
            Upsert result with store-assigned identity.
        """
        ...

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load a job by store identity.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Job posting if found, else None.
        """
        ...

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List recent jobs.

        Args:
            limit: Max jobs.

        Returns:
            Job postings by recency.
        """
        ...

    async def job_exists(self, *, platform: str, canonical_id: str) -> bool:
        """Check job existence by natural key.

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.

        Returns:
            True if the job exists.
        """
        ...

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None:
        """Persist a successful Stage A result.

        Args:
            job_id: Store-assigned identity.
            result: Stage A result.
        """
        ...

    async def save_stage_a_error(self, job_id: str, error: str) -> None:
        """Record a Stage A error (retryable).

        Args:
            job_id: Store-assigned identity.
            error: Error detail.
        """
        ...

    async def save_stage_b(self, job_id: str, result: StageBResult) -> None:
        """Persist a successful Stage B result.

        Args:
            job_id: Store-assigned identity.
            result: Stage B result.
        """
        ...

    async def save_stage_b_error(self, job_id: str, error: str) -> None:
        """Record a Stage B error (retryable).

        Args:
            job_id: Store-assigned identity.
            error: Error detail.
        """
        ...

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        """Mark Stage B as skipped (threshold decision by service).

        Args:
            job_id: Store-assigned identity.
        """
        ...

    async def load_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load jobs pending Stage A evaluation.

        Args:
            limit: Max jobs.
            quality_bands: Filter by jd_quality.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter on discovered_at.

        Returns:
            Jobs pending Stage A.
        """
        ...

    async def load_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
    ) -> list[JobPosting]:
        """Load Stage A-completed, Stage B-pending jobs.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.
            stage_a_threshold: Optional minimum Stage A score.

        Returns:
            Jobs pending Stage B.
        """
        ...

    async def list_evaluated_jobs(self, limit: int = 100) -> list[JobEvaluation]:
        """List jobs with evaluations.

        Args:
            limit: Max evaluations.

        Returns:
            Joined evaluations.
        """
        ...

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Fetch a single job's evaluation.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Evaluation if found, else None.
        """
        ...

    async def top_evaluated_jobs(
        self,
        *,
        min_score: int = 0,
        limit: int = 100,
    ) -> list[JobEvaluation]:
        """Stage B-completed jobs by score descending.

        Args:
            min_score: Score threshold.
            limit: Max jobs.

        Returns:
            Sorted evaluations.
        """
        ...

    async def save_ml_gate_result(
        self,
        job_id: str,
        result: MLGateResult,
    ) -> None:
        """Persist ML gate decision and features.

        Args:
            job_id: Store-assigned identity.
            result: Gate decision with features.
        """
        ...

    async def save_hard_filters(self, reasons: dict[str, str]) -> None:
        """Persist deterministic hard-filter reasons by job identity.

        Args:
            reasons: Store job id to human-readable exclusion reason.
        """
        ...

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load a pipeline run by identity.

        Args:
            run_id: Run identity.

        Returns:
            Pipeline run if found, else None.
        """
        ...
