"""Pydantic DTOs for the web API: request params and response shapes.

Pure mapping layer: converters take domain objects produced by the services
and render the wire shape. No store access, no composition logic. Split into
one module per route family to honor the 300-line file gate.
"""

from __future__ import annotations

from jobfeed.web.schemas.applications import (
    ApplicationRow,
    ApplicationsListResponse,
    ApplyResponse,
    applications_list_response,
)
from jobfeed.web.schemas.jobs_detail import (
    JobDetailResponse,
    interview_round_response,
    job_detail_response,
)
from jobfeed.web.schemas.jobs_list import (
    JobsListParams,
    JobsListResponse,
    JobSummary,
    jobs_list_response,
)
from jobfeed.web.schemas.workflow import (
    BulkTransitionBody,
    BulkTransitionResponse,
    FollowupBody,
    InterviewAddBody,
    InterviewCompleteBody,
    InterviewsListResponse,
    JdPasteBody,
    JdPasteResponse,
    NoteBody,
    OkResponse,
    TransitionBody,
    TransitionResponse,
    bulk_transition_response,
)

__all__ = [
    "ApplicationRow",
    "ApplicationsListResponse",
    "ApplyResponse",
    "BulkTransitionBody",
    "BulkTransitionResponse",
    "FollowupBody",
    "InterviewAddBody",
    "InterviewCompleteBody",
    "InterviewsListResponse",
    "JdPasteBody",
    "JdPasteResponse",
    "JobDetailResponse",
    "JobSummary",
    "JobsListParams",
    "JobsListResponse",
    "NoteBody",
    "OkResponse",
    "TransitionBody",
    "TransitionResponse",
    "applications_list_response",
    "bulk_transition_response",
    "interview_round_response",
    "job_detail_response",
    "jobs_list_response",
]
