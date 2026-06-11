"""Pydantic DTOs for the web API: request params and response shapes.

Pure mapping layer: converters take domain objects produced by the services
and render the wire shape. No store access, no composition logic. Split into
one module per route family to honor the 300-line file gate.
"""

from __future__ import annotations

from jobfeed.web.schemas.jobs_detail import JobDetailResponse, job_detail_response
from jobfeed.web.schemas.jobs_list import (
    JobsListParams,
    JobsListResponse,
    JobSummary,
    jobs_list_response,
)

__all__ = [
    "JobDetailResponse",
    "JobSummary",
    "JobsListParams",
    "JobsListResponse",
    "job_detail_response",
    "jobs_list_response",
]
