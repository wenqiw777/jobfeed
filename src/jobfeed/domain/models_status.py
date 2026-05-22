"""Status-related domain models for workflow and application tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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


__all__ = [
    "AutoDecayResult",
    "StatusInfo",
    "StatusTransition",
    "WorkflowAttention",
    "WorkflowAttentionItem",
]
