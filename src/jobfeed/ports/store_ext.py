"""Extended store port: batch evaluation, status/workflow, application audit."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    ResumeSnapshot,
    StatusInfo,
    WorkflowAttention,
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


@runtime_checkable
class StoreStatusMixin(Protocol):
    """Status listing, notes, and workflow queries."""

    async def list_statuses(
        self,
        *,
        statuses: frozenset[str] | None = None,
        days: int | None = None,
        no_response_days: int | None = None,
        needs_followup: bool = False,
        notes_contain: str | None = None,
        limit: int | None = None,
    ) -> list[StatusInfo]:
        """Query jobs by status with optional filters.

        Args:
            statuses: Restrict to these status values.
            days: Only changes within N days.
            no_response_days: Applied but silent for N days.
            needs_followup: Follow-up date in past or today.
            notes_contain: Substring match in notes.
            limit: Max results.

        Returns:
            Matching status info records.
        """
        ...

    async def append_note(self, *, job_id: str, text: str) -> None:
        """Append timestamped note, reset ghost clock.

        Args:
            job_id: Store-assigned job identity.
            text: Note text to append.
        """
        ...

    async def workflow_attention(
        self,
        *,
        auto_ghost_days: int = 30,
        lookahead_days: int = 5,
    ) -> WorkflowAttention:
        """Three-bucket workflow attention report.

        Args:
            auto_ghost_days: Ghost threshold.
            lookahead_days: Early warning window.

        Returns:
            Follow-up, interview prep, and going-ghosted lists.
        """
        ...

    async def compute_reapply_notice(
        self,
        *,
        job_id: str,
        lookback_days: int = 60,
    ) -> str | None:
        """Detect same-company active application.

        Args:
            job_id: Job to check.
            lookback_days: How far back to look.

        Returns:
            Notice string if detected, else None.
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
