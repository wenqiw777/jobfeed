"""Workflow service for user-driven status lifecycle and interview tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models_status import (
    AutoDecayResult,
    BulkResult,
    WorkflowAttention,
)
from jobfeed.domain.status import (
    REASON_BULK_CASCADE,
    REASON_BULK_SELECTED,
    pick_restore_target,
)
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_ext import StoreInterviewMixin
from jobfeed.ports.store_status import StoreStatusMixin

_APPLIED = "applied"
_INTERVIEWING = "interviewing"
_GHOSTED = "ghosted"
_ARCHIVED = "archived"
_RESTORABLE = (_GHOSTED, _ARCHIVED)


@runtime_checkable
class WorkflowStore(StoreStatusMixin, StoreInterviewMixin, Protocol):
    """Combined store capability required by WorkflowService."""

    async def register_resume_variant(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> bool:
        """Register a named resume variant (idempotent).

        Args:
            name: Variant name.
            description: Optional description.

        Returns:
            True if new, False if existed.
        """
        ...


class WorkflowService:
    """Orchestrates status transitions, notes, interview rounds, and housekeeping."""

    def __init__(self, store: WorkflowStore, logger: JobfeedLogger) -> None:
        """Create a workflow service.

        Args:
            store: Persistence port with status and interview capabilities.
            logger: Structured logger.
        """
        self._store = store
        self._logger = logger

    async def transition(  # noqa: PLR0913
        self,
        job_id: str,
        new_status: str,
        *,
        force: bool = False,
        i_mean_it: bool = False,
        note: str | None = None,
        resume_variant: str | None = None,
    ) -> str:
        """Transition a single job to *new_status*.

        Args:
            job_id: Store-assigned job identity.
            new_status: Target status value.
            force: Bypass the transition graph.
            i_mean_it: Required with force for archived to new.
            note: Optional note to append after the transition.
            resume_variant: Optional resume variant name.

        Returns:
            The new status string.
        """
        if resume_variant is not None:
            await self._store.register_resume_variant(name=resume_variant)
        result = await self._store.transition_status(
            job_id=job_id,
            new_status=new_status,
            force=force,
            i_mean_it=i_mean_it,
            resume_variant=resume_variant,
        )
        if note is not None:
            await self._store.append_note(job_id=job_id, text=note)
        self._logger.info(
            "workflow_transition",
            job_id=job_id,
            new_status=result,
        )
        return result

    async def transition_bulk(
        self,
        items: list[tuple[str, str]],
        *,
        force: bool = False,
        i_mean_it: bool = False,
    ) -> BulkResult:
        """Transition multiple jobs with twin-cluster cascade.

        Args:
            items: Pairs of (job_id, new_status).
            force: Bypass the transition graph.
            i_mean_it: Required with force for archived to new.

        Returns:
            Summary of succeeded, failed, and skipped transitions.
        """
        job_ids = [int(jid) for jid, _ in items]
        await self._store.expand_twin_ids(job_ids)
        result = await self._store.transition_status_bulk(
            items,
            reason_selected=REASON_BULK_SELECTED,
            reason_cascade=REASON_BULK_CASCADE,
            force=force,
            i_mean_it=i_mean_it,
        )
        self._logger.info(
            "workflow_transition_bulk",
            succeeded=result.succeeded,
            failed=len(result.failed),
            skipped=result.skipped,
        )
        return result

    async def restore(self, job_id: str) -> str:
        """Restore a ghosted/archived job to its most recent non-terminal status.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            The restored status string.

        Raises:
            ValueError: If the job is not ghosted or archived.
        """
        status_info = await self._store.get_status(job_id)
        current = getattr(status_info, "status", None) if status_info else None
        if current not in _RESTORABLE:
            msg = f"restore requires ghosted or archived, got {current!r} for {job_id}"
            raise ValueError(msg)
        history = await self._store.get_status_history(job_id)
        target = pick_restore_target(history) or _APPLIED
        result = await self._store.transition_status(
            job_id=job_id,
            new_status=target,
            force=True,
            i_mean_it=True,
            reason="restore",
        )
        self._logger.info(
            "workflow_restore",
            job_id=job_id,
            restored_to=result,
        )
        return result

    async def note(self, job_id: str, text: str) -> None:
        """Append a note to a job and reset its ghost clock.

        Args:
            job_id: Store-assigned job identity.
            text: Note content.
        """
        await self._store.append_note(job_id=job_id, text=text)
        self._logger.info("workflow_note_appended", job_id=job_id)

    async def add_round(
        self,
        job_id: str,
        label: str,
        *,
        scheduled_at: datetime | None = None,
    ) -> InterviewRound:
        """Add an interview round, auto-transitioning from applied if needed.

        Args:
            job_id: Store-assigned job identity.
            label: Human-readable round label.
            scheduled_at: Optional scheduled interview time.

        Returns:
            The newly created interview round.
        """
        status_info = await self._store.get_status(job_id)
        if status_info is not None and getattr(status_info, "status", None) == _APPLIED:
            await self._store.transition_status(
                job_id=job_id,
                new_status=_INTERVIEWING,
            )
            self._logger.info(
                "workflow_auto_transition",
                job_id=job_id,
                from_status=_APPLIED,
                to_status=_INTERVIEWING,
            )
        return await self._store.add_interview_round(
            job_id=job_id,
            label=label,
            scheduled_at=scheduled_at,
        )

    async def list_rounds(self, job_id: str) -> list[InterviewRound]:
        """List interview rounds for a job, ascending by round_index.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Interview rounds ordered by round_index.
        """
        return await self._store.list_interview_rounds(job_id)

    async def complete_round(
        self,
        job_id: str,
        *,
        round_index: int | None = None,
        notes: str | None = None,
    ) -> InterviewRound:
        """Mark an interview round as completed.

        Args:
            job_id: Store-assigned job identity.
            round_index: Specific round, or None for latest open.
            notes: Optional notes to attach.

        Returns:
            The completed interview round.
        """
        return await self._store.complete_interview_round(
            job_id=job_id,
            round_index=round_index,
            notes=notes,
        )

    async def attention(
        self,
        *,
        auto_ghost_days: int = 30,
        lookahead_days: int = 5,
    ) -> WorkflowAttention:
        """Three-bucket workflow attention report.

        Args:
            auto_ghost_days: Ghost threshold in days.
            lookahead_days: Early warning window in days.

        Returns:
            Follow-up, interview prep, and going-ghosted lists.
        """
        return await self._store.workflow_attention(
            auto_ghost_days=auto_ghost_days,
            lookahead_days=lookahead_days,
        )

    async def auto_decay(
        self,
        *,
        ghost_days: int = 30,
        archive_ignored_days: int = 14,
    ) -> AutoDecayResult:
        """Sweep stale jobs to ghosted/archived.

        Args:
            ghost_days: Days before applied/interviewing is ghosted.
            archive_ignored_days: Days before ignored is archived.

        Returns:
            Counts of ghosted and archived jobs.
        """
        result = await self._store.auto_decay(
            ghost_days=ghost_days,
            archive_ignored_days=archive_ignored_days,
        )
        self._logger.info(
            "workflow_auto_decay",
            ghosted=result.ghosted,
            archived=result.archived,
        )
        return result


__all__ = ["WorkflowService", "WorkflowStore"]
