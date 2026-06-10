"""Status & workflow capability port.

The full job-status lifecycle and workflow surface in one place: transition /
get / restore, the auto-decay sweep, status listing + notes, the
workflow-attention report, and same-company reapply detection. A backend
implements this as an optional capability alongside the core JobStore.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    AutoDecayResult,
    BulkResult,
    StatusInfo,
    WorkflowAttention,
)


@runtime_checkable
class StoreStatusMixin(Protocol):
    """Status lifecycle, listing, notes, and workflow queries."""

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
        """Transition a job's status with validation and history.

        Args:
            job_id: Store-assigned identity.
            new_status: Target status.
            reason: Optional reason tag.
            resume_variant: Optional variant name.
            force: Bypass transition graph.
            i_mean_it: Required with force for archived to new.
            followup_grace_days: Days until next follow-up.

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
            no_response_days: Applied/interviewing but silent for N days.
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

    async def get_status_history(self, job_id: str) -> list[str]:
        """Return to_status values from job_status_history, newest-first.

        Args:
            job_id: Store-assigned identity.

        Returns:
            List of status strings in reverse chronological order.
        """
        ...

    async def expand_twin_ids(self, job_ids: list[int]) -> dict[int, list[int]]:
        """Expand each job_id to its twin cluster (same company_norm + title_norm).

        A row with blank company_norm or title_norm expands to itself only.

        Args:
            job_ids: Store-assigned job identities.

        Returns:
            Mapping of job_id to list of twin cluster member ids.
        """
        ...

    async def transition_status_bulk(
        self,
        items: list[tuple[str, str]],
        *,
        reason_selected: str,
        reason_cascade: str,
        force: bool = False,
        i_mean_it: bool = False,
    ) -> BulkResult:
        """Transition each item plus its twin cluster. Atomic per cluster.

        The selected job gets reason_selected, twins get reason_cascade.
        Each cluster is transitioned in its own transaction; a failing
        cluster is recorded and does not block others.

        Args:
            items: Pairs of (job_id, new_status).
            reason_selected: Reason tag for the explicitly selected job.
            reason_cascade: Reason tag for twin-cluster siblings.
            force: Bypass the transition graph.
            i_mean_it: Required alongside force for archived to new.

        Returns:
            Summary of succeeded, failed, and skipped transitions.
        """
        ...
