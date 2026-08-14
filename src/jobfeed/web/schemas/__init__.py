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
from jobfeed.web.schemas.companies import (
    REMOVED_SENTINEL,
    BulkCompanyRow,
    BulkInsertResponse,
    CompaniesBulkBody,
    CompaniesListResponse,
    CompanyAddBody,
    CompanyOut,
    ProbeBody,
    ProbeEntryResult,
    ProbeResponse,
    VendorName,
    company_out,
)
from jobfeed.web.schemas.insights import (
    AttentionResponse,
    InsightsDayEntry,
    InsightsOverviewResponse,
    InsightsTotals,
    PipelineAttentionEntry,
    WorkflowAttentionEntry,
    attention_response,
    insights_overview_response,
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
from jobfeed.web.schemas.runs import (
    RunsListResponse,
    RunSummary,
    run_summary,
    runs_list_response,
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
    RestoreResponse,
    TransitionBody,
    TransitionResponse,
    bulk_transition_response,
)

__all__ = [
    "REMOVED_SENTINEL",
    "ApplicationRow",
    "ApplicationsListResponse",
    "ApplyResponse",
    "AttentionResponse",
    "BulkCompanyRow",
    "BulkInsertResponse",
    "BulkTransitionBody",
    "BulkTransitionResponse",
    "CompaniesBulkBody",
    "CompaniesListResponse",
    "CompanyAddBody",
    "CompanyOut",
    "FollowupBody",
    "InsightsDayEntry",
    "InsightsOverviewResponse",
    "InsightsTotals",
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
    "PipelineAttentionEntry",
    "ProbeBody",
    "ProbeEntryResult",
    "ProbeResponse",
    "RestoreResponse",
    "RunSummary",
    "RunsListResponse",
    "TransitionBody",
    "TransitionResponse",
    "VendorName",
    "WorkflowAttentionEntry",
    "applications_list_response",
    "attention_response",
    "bulk_transition_response",
    "company_out",
    "insights_overview_response",
    "interview_round_response",
    "job_detail_response",
    "jobs_list_response",
    "run_summary",
    "runs_list_response",
]
