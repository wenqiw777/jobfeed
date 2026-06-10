"""Status-related domain models for workflow and application tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported only for annotations (postponed via __future__): a runtime import
    # would create a cycle, since jobfeed.domain.models re-exports this module.
    from jobfeed.domain.models import JobStatus

_DEFAULT_FOLLOWUP_GRACE_DAYS = 7


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


@dataclass(frozen=True, kw_only=True)
class TransitionRequest:
    """Parameters for a single status transition."""

    job_id: str
    new_status: str
    reason: str | None = None
    resume_variant: str | None = None
    force: bool = False
    i_mean_it: bool = False
    followup_grace_days: int = _DEFAULT_FOLLOWUP_GRACE_DAYS


@dataclass(frozen=True, kw_only=True)
class BulkTransitionRequest:
    """Parameters for a bulk twin-cascade transition."""

    items: list[tuple[str, str]]
    reason_selected: str
    reason_cascade: str
    force: bool = False
    i_mean_it: bool = False


@dataclass(frozen=True, kw_only=True)
class StatusFilter:
    """Filter parameters for list_statuses queries."""

    statuses: frozenset[str] | None = None
    days: int | None = None
    no_response_days: int | None = None
    needs_followup: bool = False
    notes_contain: str | None = None
    limit: int | None = None


__all__ = [
    "AutoDecayResult",
    "BulkResult",
    "BulkTransitionRequest",
    "StatusFilter",
    "StatusInfo",
    "StatusTransition",
    "TransitionRequest",
    "WorkflowAttention",
    "WorkflowAttentionItem",
]
