"""Extended store port: batch evaluation, stage-B preview, application audit."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    JobPosting,
    ResumeSnapshot,
)


@runtime_checkable
class StoreEvaluationBatchMixin(Protocol):
    """Batch evaluation queries for service-layer efficiency."""

    async def get_stage_a_scores(self, job_ids: list[str]) -> dict[str, int | None]:
        """Batch-fetch Stage A scores.

        Args:
            job_ids: Store-assigned job identities.

        Returns:
            Mapping of job_id to stage_a_score (None if unevaluated).
        """
        ...

    async def mark_stage_b_skipped_batch(self, job_ids: list[str]) -> None:
        """Mark multiple Stage B evaluations as skipped in a single operation.

        Args:
            job_ids: Store-assigned job identities to skip.
        """
        ...

    async def mark_stage_b_below_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        """Mark pending Stage B rows whose stored Stage A score is below threshold.

        Args:
            threshold: Minimum Stage A score allowed into Stage B.
            max_days: Optional freshness window on discovered_at.

        Returns:
            Number of rows marked skipped.
        """
        ...

    async def reopen_stage_b_at_or_above_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        """Reopen threshold-skipped rows that now meet the active threshold.

        Args:
            threshold: Minimum Stage A score allowed into Stage B.
            max_days: Optional freshness window on discovered_at.

        Returns:
            Number of rows reopened.
        """
        ...


@runtime_checkable
class StoreStageBPreviewMixin(Protocol):
    """Read-only Stage B queue preview helpers."""

    async def preview_pending_stage_b_after_threshold_sync(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int,
    ) -> list[JobPosting]:
        """Preview Stage B jobs after threshold requeue/skip sync.

        Args:
            limit: Max jobs.
            max_days: Optional freshness window on discovered_at.
            stage_a_threshold: Active Stage A threshold.

        Returns:
            Jobs a real Stage B run would consider after threshold sync,
            without mutating evaluation status.
        """
        ...


@runtime_checkable
class StoreApplicationMixin(Protocol):
    """Application audit trail and resume snapshot methods."""

    async def record_application(self, record: ApplicationRecord) -> bool:
        """Record application with atomic status transition.

        Args:
            record: Application audit record.

        Returns:
            True if new, False if already applied.
        """
        ...

    async def list_applications(
        self,
        *,
        limit: int = 100,
    ) -> list[ApplicationRecord]:
        """List application records by recency.

        Args:
            limit: Max records.

        Returns:
            Application records.
        """
        ...

    async def application_stats(
        self,
        *,
        since_days_ago: int = 30,
        by_resume: bool = False,
    ) -> ApplicationStats:
        """Aggregate application statistics.

        Args:
            since_days_ago: Time window.
            by_resume: Include per-variant breakdown.

        Returns:
            Application statistics.
        """
        ...

    async def save_resume_snapshot(self, snapshot: ResumeSnapshot) -> None:
        """Content-addressed resume insert (no-op if exists).

        Args:
            snapshot: Resume snapshot to persist.
        """
        ...

    async def get_resume_snapshot(
        self,
        resume_hash: str,
    ) -> ResumeSnapshot | None:
        """Load resume snapshot by hash.

        Args:
            resume_hash: Content-addressed hash.

        Returns:
            Snapshot if found, else None.
        """
        ...

    async def register_resume_variant(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> bool:
        """Register a named resume variant.

        Args:
            name: Variant name.
            description: Optional description.

        Returns:
            True if new, False if existed.
        """
        ...
