"""Status-related domain models for workflow and application tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported only for annotations (postponed via __future__): a runtime import
    # would create a cycle, since jobfeed.domain.models re-exports this module.
    from jobfeed.domain.models import JobStatus


@dataclass(kw_only=True)
class StatusTransition:
    """One row in the job_status_history append-only log."""

    job_id: str
    from_status: JobStatus | None
    to_status: JobStatus
    reason: str | None = None
    changed_at: datetime
    resume_variant: str | None = None


@dataclass(kw_only=True)
class StatusInfo:
    """Current status state for a single job."""

    job_id: str
    status: JobStatus
    next_followup_at: datetime | None = None
    resume_variant: str | None = None
    notes: str | None = None
    last_status_change_at: datetime


@dataclass(kw_only=True)
class AutoDecayResult:
    """Counts returned by auto_decay sweep."""

    ghosted: int
    archived: int


@dataclass(kw_only=True)
class WorkflowAttentionItem:
    """Single item in a workflow attention list."""

    job_id: str
    title: str
    company: str
    url: str
    status: str
    last_status_change_at: datetime
    next_followup_at: datetime | None
    notes: str | None
    reason: str
    days_since: int


@dataclass(kw_only=True)
class WorkflowAttention:
    """Three-bucket workflow attention report."""

    follow_up_today: list[WorkflowAttentionItem]
    interview_prep: list[WorkflowAttentionItem]
    going_ghosted: list[WorkflowAttentionItem]


@dataclass(kw_only=True)
class BulkResult:
    """Result of a bulk status transition with twin cascade."""

    succeeded: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (job_id, error)
    skipped: int = 0  # terminal jobs in cluster


__all__ = [
    "AutoDecayResult",
    "BulkResult",
    "StatusInfo",
    "StatusTransition",
    "WorkflowAttention",
    "WorkflowAttentionItem",
]
