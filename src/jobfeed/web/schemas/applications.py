"""DTOs for the apply route and the applications history endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jobfeed.domain.models import ApplicationRecord, RealJobApplicationEvent


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


class RealJobApplicationRow(BaseModel):
    """Canonical submission event with source and target provenance."""

    id: int
    real_job_id: str
    source_job_id: str | None
    source_applied_job_id: str | None
    apply_url: str | None
    applied_at: datetime
    application_method: str | None
    notes: str | None


class RealJobApplicationsListResponse(BaseModel):
    """Canonical application history returned by the list endpoint."""

    applications: list[RealJobApplicationRow]


def real_job_applications_response(
    events: list[RealJobApplicationEvent],
) -> RealJobApplicationsListResponse:
    """Serialize canonical application events for the API.

    Args:
        events: Canonical application events to serialize.

    Returns:
        API response containing the serialized application events.
    """
    return RealJobApplicationsListResponse(
        applications=[RealJobApplicationRow(**vars(event)) for event in events]
    )


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
    "RealJobApplicationRow",
    "RealJobApplicationsListResponse",
    "applications_list_response",
    "real_job_applications_response",
]
