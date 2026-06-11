"""DTOs for the apply route and the applications history endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jobfeed.domain.models import ApplicationRecord


class ApplyResponse(BaseModel):
    """``POST /api/jobs/{id}/apply`` response.

    ``applied`` is False for the no-op parity case (already applied);
    ``reapply_notice`` is set only on a new apply with a same-company
    active application.
    """

    applied: bool
    reapply_notice: str | None


class ApplicationRow(BaseModel):
    """One applications-history row: audit metadata + snapshot hashes only."""

    job_id: str
    applied_at: datetime
    application_method: str | None
    notes: str | None
    master_resume_hash: str | None
    tailored_resume_hash: str | None


class ApplicationsListResponse(BaseModel):
    """``GET /api/applications`` response."""

    applications: list[ApplicationRow]


def applications_list_response(
    records: list[ApplicationRecord],
) -> ApplicationsListResponse:
    """Render application records as the history response.

    Args:
        records: Application records ordered by recency.

    Returns:
        Wire-shape applications list.
    """
    return ApplicationsListResponse(
        applications=[_application_row(record) for record in records]
    )


def _application_row(record: ApplicationRecord) -> ApplicationRow:
    """Map one application record to its row DTO (never snapshot contents)."""
    return ApplicationRow(
        job_id=record.job_id,
        applied_at=record.applied_at,
        application_method=record.application_method,
        notes=record.notes,
        master_resume_hash=record.master_resume_hash,
        tailored_resume_hash=record.tailored_resume_hash,
    )


__all__ = [
    "ApplicationRow",
    "ApplicationsListResponse",
    "ApplyResponse",
    "applications_list_response",
]
