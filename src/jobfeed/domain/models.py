"""Domain dataclasses and enums for the Jobfeed pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from jobfeed.domain.types import Severity

_MIN_SCORE = 0
_MAX_SCORE = 100


class QualityBand(StrEnum):
    """JD extraction quality bands used to route enrichment and evaluation."""

    FULL = "full"
    GOOD = "good"
    PARTIAL = "partial"
    STUB = "stub"
    MISSING = "missing"
    ABANDONED = "abandoned"


class Verdict(StrEnum):
    """Stage B application recommendation values."""

    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


class JobStatus(StrEnum):
    """Lifecycle status values for job workflow and application tracking."""

    NEW = "new"
    SCORED = "scored"
    SHORTLISTED = "shortlisted"
    APPLIED = "applied"
    OA = "oa"
    HR_CALL = "hr_call"
    SECOND_ROUND = "second_round"
    FINAL_ROUND = "final_round"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    ARCHIVED = "archived"
    IGNORED = "ignored"


@dataclass(kw_only=True)
class JobPosting:
    """A source-specific job posting observed by the pipeline."""

    id: str | None = None
    platform: str
    canonical_id: str
    url: str
    title: str
    company: str
    location: str
    discovered_at: datetime
    jd_text: str | None = None
    jd_quality: QualityBand | None = None
    posted_at: datetime | None = None
    enriched_at: datetime | None = None
    enrich_source: str | None = None


@dataclass(kw_only=True)
class StageAResult:
    """Fast scoring result for initial job ranking."""

    score: int
    one_line: str
    timing_eligible: str
    model: str
    prompt_hash: str
    resume_hash: str
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        _validate_score(self.score)


@dataclass(kw_only=True)
class MatchItem:
    """A matched requirement and the resume evidence supporting it."""

    requirement: str
    evidence: str


@dataclass(kw_only=True)
class GapItem:
    """A missing or weak requirement with severity and mitigation guidance."""

    requirement: str
    severity: Severity
    mitigation: str


@dataclass(kw_only=True)
class FitAnalysis:
    """Normalized Stage B fit analysis with strengths and gaps."""

    score: int
    strengths: list[MatchItem]
    gaps: list[GapItem]

    def __post_init__(self) -> None:
        _validate_score(self.score)


@dataclass(kw_only=True)
class StageBResult:
    """Detailed LLM evaluation result for shortlist and apply decisions."""

    verdict: Verdict
    jd_summary: str
    fit_analysis: FitAnalysis
    resume_hooks: list[str]
    model: str
    prompt_hash: str
    resume_hash: str
    cost_usd: float | None = None
    raw_blocks: dict[str, object] | None = None


@dataclass(kw_only=True)
class MLGateResult:
    """Machine-learning gate decision before paid LLM scoring."""

    score: float
    result: str
    fail_reason: str | None = None
    version: str | None = None
    is_swe_role: bool | None = None
    seniority_level: str | None = None
    degree_required: str | None = None
    clearance_required: bool | None = None
    school_restricted: bool | None = None
    yoe_min: int | None = None
    domain_tags: list[str] | None = None
    tech_required: list[str] | None = None
    role_type: str | None = None

    def __post_init__(self) -> None:
        """Validate the gate score range and result enum.

        Raises:
            ValueError: If score is outside [0, 1] or result is not a recognized
                pass/fail value.
        """
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"ml gate score out of range [0, 1]: {self.score}")
        if self.result not in ("pass", "fail"):
            raise ValueError(
                f"ml gate result must be 'pass' or 'fail': {self.result!r}"
            )


@dataclass(kw_only=True)
class SaveJobResult:
    """Persistence upsert result for a source job row."""

    job_id: str
    inserted: bool
    updated: bool


@dataclass(kw_only=True)
class PipelineRun:
    """Aggregate counters and timing for a scan or evaluation run."""

    run_id: str
    started_at: datetime
    source: str
    jobs_discovered: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_filtered: int = 0
    jobs_ml_gated: int = 0
    stage_a_scored: int = 0
    stage_b_scored: int = 0
    jobs_scored: int = 0
    total_llm_cost_usd: float = 0.0
    errors: int = 0
    finished_at: datetime | None = None


@dataclass(kw_only=True)
class JobEvaluation:
    """A job with optional Stage A and Stage B evaluation results."""

    job: JobPosting
    stage_a: StageAResult | None
    stage_b: StageBResult | None


def _validate_score(score: int) -> None:
    if isinstance(score, bool) or not isinstance(score, int):
        raise ValueError("score must be an integer between 0 and 100")
    if score < _MIN_SCORE or score > _MAX_SCORE:
        raise ValueError("score must be an integer between 0 and 100")


from jobfeed.domain.models_application import (  # noqa: E402
    ApplicationRecord,
    ApplicationStats,
    ResumeSnapshot,
    ResumeVariant,
    ResumeVariantStats,
)
from jobfeed.domain.models_llm import (  # noqa: E402
    LLMRequest,
    LLMResponse,
    LLMUsage,
    Message,
)
from jobfeed.domain.models_ops import (  # noqa: E402
    AttentionItem,
    AttentionReport,
    CompanyRecord,
    CostEntry,
    DigestStats,
)
from jobfeed.domain.models_status import (  # noqa: E402
    AutoDecayResult,
    StatusInfo,
    StatusTransition,
    WorkflowAttention,
    WorkflowAttentionItem,
)

__all__ = [
    "ApplicationRecord",
    "ApplicationStats",
    "AttentionItem",
    "AttentionReport",
    "AutoDecayResult",
    "CompanyRecord",
    "CostEntry",
    "DigestStats",
    "FitAnalysis",
    "GapItem",
    "JobEvaluation",
    "JobPosting",
    "JobStatus",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "MLGateResult",
    "MatchItem",
    "Message",
    "PipelineRun",
    "QualityBand",
    "ResumeSnapshot",
    "ResumeVariant",
    "ResumeVariantStats",
    "SaveJobResult",
    "StageAResult",
    "StageBResult",
    "StatusInfo",
    "StatusTransition",
    "Verdict",
    "WorkflowAttention",
    "WorkflowAttentionItem",
]
