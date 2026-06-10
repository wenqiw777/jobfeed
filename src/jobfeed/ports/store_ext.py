"""Extended store port: batch evaluation, stage-B preview, application audit."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.interview import InterviewRound
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

    async def record_application_with_snapshots(
        self,
        record: ApplicationRecord,
        *,
        snapshots: list[ResumeSnapshot] | None = None,
        resume_variant: str | None = None,
    ) -> bool:
        """Record application with resume snapshots in one atomic transaction.

        Upserts snapshots, inserts the applied row, checks idempotency,
        guards terminal status, transitions to applied, and optionally
        auto-registers the resume variant -- all inside a single transaction.

        Args:
            record: Application audit record.
            snapshots: Optional resume snapshots to persist atomically.
            resume_variant: Optional variant name to set on the job status;
                auto-registered if not already known.

        Returns:
            True if new, False if already applied.

        Raises:
            ValueError: If the job is in a terminal status.
        """
        ...

    async def get_application(self, job_id: str) -> ApplicationRecord | None:
        """Load a single application record by job_id.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Application record if found, else None.
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


@runtime_checkable
class StoreInterviewMixin(Protocol):
    """Interview round CRUD for per-job interview tracking."""

    async def add_interview_round(
        self,
        *,
        job_id: str,
        label: str,
        scheduled_at: datetime | None = None,
    ) -> InterviewRound:
        """Append a new interview round to a job.

        Assigns round_index automatically (max existing + 1).

        Args:
            job_id: Store-assigned job identity.
            label: Human-readable round label (e.g. "Phone Screen").
            scheduled_at: Optional scheduled interview time.

        Returns:
            The newly created interview round.
        """
        ...

    async def list_interview_rounds(self, job_id: str) -> list[InterviewRound]:
        """List all interview rounds for a job, ordered by round_index.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Interview rounds in ascending round_index order.
        """
        ...

    async def complete_interview_round(
        self,
        *,
        job_id: str,
        round_index: int | None = None,
        notes: str | None = None,
    ) -> InterviewRound:
        """Mark an interview round as completed.

        If round_index is None, completes the latest open round.

        Args:
            job_id: Store-assigned job identity.
            round_index: Specific round to complete, or None for latest open.
            notes: Optional notes to attach.

        Returns:
            The completed interview round.

        Raises:
            ValueError: If no open interview round exists for the job.
        """
        ...

    async def list_upcoming_interviews(
        self,
        *,
        within_days: int = 7,
    ) -> list[InterviewRound]:
        """List scheduled but not-yet-completed interviews within a time window.

        Args:
            within_days: Number of days ahead to look.

        Returns:
            Upcoming interview rounds ordered by scheduled_at.
        """
        ...
