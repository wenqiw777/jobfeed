"""Canonical posting evidence consumed by priority calculation."""

from dataclasses import dataclass

from jobfeed.domain.models import JobPosting


@dataclass(frozen=True)
class CanonicalPriorityInput:
    """Source evidence and canonical scoring facts for one real job."""

    real_job_id: str
    source_job_id: str
    job: JobPosting
    status: str
    stage_a_score: int | None
    stage_b_fit_score: int | None
    stage_a_status: str | None = None
    stage_b_status: str | None = None
    input_facts_json: str | None = None
