"""Extended store port: batch evaluation, stage-B preview, interviews.

The application-audit capability lives in ``store_application.py``
(split out to keep both modules under the 300-line gate).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models import JobPosting


@runtime_checkable
class StageBThresholdSync(Protocol):
    """Synchronize Stage B eligibility to one active Stage A threshold."""

    async def sync_stage_b_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> tuple[int, int]:
        """Reopen eligible rows and skip ineligible rows.

        Args:
            threshold: Minimum Stage A score allowed into Stage B.
            max_days: Optional freshness window on discovered_at.

        Returns:
            Reopened-row count followed by skipped-row count.
        """
        ...


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
        """List future scheduled, incomplete interviews within a time window.

        Args:
            within_days: Number of days ahead to look.

        Returns:
            Upcoming interview rounds whose parent job is interviewing,
            ordered by scheduled_at.
        """
        ...
