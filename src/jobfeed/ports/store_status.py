"""Status & workflow capability port.

The full job-status lifecycle and workflow surface in one place: transition /
get / restore, the auto-decay sweep, status listing + notes, the
workflow-attention report, and same-company reapply detection. A backend
implements this as an optional capability alongside the core JobStore.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    StatusFilter,
    StatusInfo,
    TransitionRequest,
    WorkflowAttention,
)


@runtime_checkable
class StoreStatusMixin(Protocol):
    """Status lifecycle, listing, notes, and workflow queries."""

    async def transition_status(self, request: TransitionRequest) -> str:
        """Transition a job's status with validation and history.

        Args:
            request: Transition parameters (job_id, new_status, reason,
                resume_variant, force, i_mean_it, followup_grace_days).

        Returns:
            The new status string.
        """
        ...

    async def get_status(self, job_id: str) -> StatusInfo | None:
        """Get current status for a job.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Status info if found, else None.
        """
        ...

    async def restore_from_archived(self, job_id: str) -> str:
        """Restore archived job to pre-archive status.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Restored status string.
        """
        ...

    async def auto_decay(
        self,
        *,
        ghost_days: int = 30,
        archive_ignored_days: int = 14,
    ) -> AutoDecayResult:
        """Sweep stale jobs to ghosted/archived.

        Args:
            ghost_days: Days before ghosting.
            archive_ignored_days: Days before archiving ignored.

        Returns:
            Counts of ghosted and archived jobs.
        """
        ...

    async def list_statuses(
        self, filters: StatusFilter | None = None
    ) -> list[StatusInfo]:
        """Query jobs by status with optional filters.

        Args:
            filters: Filter parameters (statuses, days, since,
                no_response_days, needs_followup, notes_contain, limit).

        Returns:
            Matching status info records.
        """
        ...

    async def append_note(self, *, job_id: str, text: str) -> bool:
        """Append timestamped note, reset ghost clock.

        Args:
            job_id: Store-assigned job identity.
            text: Note text to append.

        Returns:
            True if a job_status row was updated, False if none exists.
        """
        ...

    async def set_followup(self, *, job_id: str, at: datetime) -> bool:
        """Set the next follow-up time for a job.

        Args:
            job_id: Store-assigned job identity.
            at: When the next follow-up is due.

        Returns:
            True if a job_status row was updated, False if none exists.
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

    async def get_status_history(self, job_id: str) -> list[str]:
        """Return to_status values from job_status_history, newest-first.

        Args:
            job_id: Store-assigned identity.

        Returns:
            List of status strings in reverse chronological order.
        """
        ...

    async def transition_status_bulk(
        self, request: BulkTransitionRequest
    ) -> BulkResult:
        """Transition each item plus its twin cluster. Atomic per cluster.

        The selected job gets reason_selected, twins get reason_cascade.
        Each cluster is transitioned in its own transaction; a failing
        cluster is recorded and does not block others.

        Args:
            request: Bulk transition parameters (items, reason_selected,
                reason_cascade, force, i_mean_it).

        Returns:
            Summary of succeeded, failed, and skipped transitions.
        """
        ...
