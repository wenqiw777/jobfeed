"""DTOs for the workflow routes: transitions, notes, JD paste, interviews."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from jobfeed.domain.models_status import BulkResult
from jobfeed.web.schemas.jobs_detail import InterviewRoundDetail

# Mirrors domain STATUS_VALUES so unknown statuses fail validation (422)
# instead of reaching the transition graph (409). Pinned by a drift guard
# in tests/unit/test_web_schema_constants.py.
StatusName = Literal[
    "new",
    "scored",
    "shortlisted",
    "awaiting_referral",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "ghosted",
    "archived",
    "ignored",
]


def _require_non_blank(value: str) -> str:
    """Reject empty or whitespace-only text in a field validator."""
    if not value.strip():
        raise ValueError("must not be empty or whitespace-only")
    return value


class TransitionBody(BaseModel):
    """``POST /api/jobs/{id}/transition`` request body."""

    to: StatusName
    note: str | None = None
    force: bool = False


class TransitionResponse(BaseModel):
    """Single-transition response: the job's resulting status."""

    job_id: str
    status: str


class BulkTransitionItem(BaseModel):
    """One requested transition of a bulk request."""

    id: str = Field(min_length=1)
    to: StatusName


class BulkTransitionBody(BaseModel):
    """``POST /api/jobs/bulk/transition`` request body."""

    items: list[BulkTransitionItem] = Field(min_length=1)
    force: bool = False


class BulkFailedItem(BaseModel):
    """One failed transition of a bulk response."""

    id: str
    error: str


class BulkTransitionResponse(BaseModel):
    """Bulk-transition outcome summary (cascaded = twin transitions)."""

    succeeded: int
    skipped: int
    failed: list[BulkFailedItem]
    cascaded: int


class NoteBody(BaseModel):
    """``POST /api/jobs/{id}/note`` request body."""

    text: str

    _non_blank = field_validator("text")(_require_non_blank)


class FollowupBody(BaseModel):
    """``POST /api/jobs/{id}/followup`` request body (ISO datetime)."""

    at: AwareDatetime = Field(
        description="Follow-up time; must carry a UTC offset (naive -> 422)."
    )


class OkResponse(BaseModel):
    """Generic acknowledgement for side-effect-only routes."""

    ok: bool = True


class RestoreResponse(BaseModel):
    """``POST /api/jobs/{id}/restore`` response: where the job landed."""

    job_id: str
    status: str


class JdPasteBody(BaseModel):
    """``POST /api/jobs/{id}/jd`` request body."""

    text: str

    _non_blank = field_validator("text")(_require_non_blank)


class JdPasteResponse(BaseModel):
    """JD paste response: the job's stored assessed quality band."""

    job_id: str
    jd_quality: str | None


class InterviewAddBody(BaseModel):
    """``POST /api/jobs/{id}/interviews`` request body."""

    label: str
    scheduled_at: AwareDatetime | None = Field(
        default=None,
        description="Interview time; must carry a UTC offset (naive -> 422).",
    )

    _non_blank = field_validator("label")(_require_non_blank)


class InterviewCompleteBody(BaseModel):
    """``PATCH /api/jobs/{id}/interviews/{index}`` request body."""

    notes: str | None = None


class InterviewsListResponse(BaseModel):
    """``GET /api/jobs/{id}/interviews`` response."""

    interviews: list[InterviewRoundDetail]


def bulk_transition_response(result: BulkResult) -> BulkTransitionResponse:
    """Render a domain bulk result as the bulk-transition response.

    Args:
        result: Bulk transition outcome from the workflow service.

    Returns:
        Wire-shape bulk response.
    """
    return BulkTransitionResponse(
        succeeded=result.succeeded,
        skipped=result.skipped,
        failed=[BulkFailedItem(id=jid, error=error) for jid, error in result.failed],
        cascaded=result.cascaded,
    )


__all__ = [
    "BulkFailedItem",
    "BulkTransitionBody",
    "BulkTransitionItem",
    "BulkTransitionResponse",
    "FollowupBody",
    "InterviewAddBody",
    "InterviewCompleteBody",
    "InterviewsListResponse",
    "JdPasteBody",
    "JdPasteResponse",
    "NoteBody",
    "OkResponse",
    "RestoreResponse",
    "StatusName",
    "TransitionBody",
    "TransitionResponse",
    "bulk_transition_response",
]
