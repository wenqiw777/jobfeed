"""Persistence port protocol for Jobfeed domain objects."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    JobEvaluation,
    JobPosting,
    PipelineRun,
    SaveJobResult,
    StageAResult,
    StageBResult,
)


@runtime_checkable
class JobStore(Protocol):
    """Persistence capability required by scan, evaluate, and digest services."""

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Persist or update a source job by natural identity.

        Args:
            job: Job posting to persist.

        Returns:
            Upsert result with the store-assigned job identity.
        """
        ...

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load a job by store identity.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Job posting when found; otherwise None.
        """
        ...

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List recently discovered jobs.

        Args:
            limit: Maximum jobs to return.

        Returns:
            Job postings ordered by adapter-defined recency.
        """
        ...

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None:
        """Persist a successful Stage A result.

        Args:
            job_id: Store-assigned job identity.
            result: Stage A result to persist.
        """
        ...

    async def save_stage_a_error(self, job_id: str, error: str) -> None:
        """Persist a Stage A error without modifying Stage B state.

        Args:
            job_id: Store-assigned job identity.
            error: Error detail to persist.
        """
        ...

    async def save_stage_b(self, job_id: str, result: StageBResult) -> None:
        """Persist a successful Stage B result.

        Args:
            job_id: Store-assigned job identity.
            result: Stage B result to persist.
        """
        ...

    async def save_stage_b_error(self, job_id: str, error: str) -> None:
        """Persist a Stage B error without modifying Stage A state.

        Args:
            job_id: Store-assigned job identity.
            error: Error detail to persist.
        """
        ...

    async def load_pending_stage_a(self, limit: int = 100) -> list[JobPosting]:
        """Load jobs that have not completed Stage A evaluation.

        Args:
            limit: Maximum jobs to return.

        Returns:
            Jobs pending Stage A.
        """
        ...

    async def load_pending_stage_b(
        self,
        threshold: int = 60,
        limit: int = 100,
    ) -> list[JobPosting]:
        """Load jobs eligible for Stage B evaluation.

        Args:
            threshold: Minimum Stage A score required for Stage B.
            limit: Maximum jobs to return.

        Returns:
            Jobs pending Stage B.
        """
        ...

    async def list_evaluated_jobs(self, limit: int = 100) -> list[JobEvaluation]:
        """List jobs with persisted evaluation state.

        Args:
            limit: Maximum evaluations to return.

        Returns:
            Job evaluations for digest rendering.
        """
        ...

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Persist aggregate pipeline run metrics.

        Args:
            run: Pipeline run counters and timing.
        """
        ...

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load a pipeline run by identity.

        Args:
            run_id: Pipeline run identity.

        Returns:
            Pipeline run when found; otherwise None.
        """
        ...

    async def connect(self) -> None:
        """Open backing store resources."""
        ...

    async def close(self) -> None:
        """Close backing store resources."""
        ...
