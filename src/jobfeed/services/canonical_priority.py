"""On-demand real-job priority without source duplicates or new fingerprints."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from jobfeed.domain.job_priority import PriorityResult, priority_for_job
from jobfeed.domain.models import JobPosting
from jobfeed.domain.real_job_evaluation import select_real_job_input


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


@dataclass(frozen=True)
class CanonicalPriorityRow:
    """One computed priority row keyed by a real job ID."""

    real_job_id: str
    source_job_id: str
    job: JobPosting
    status: str
    stage_a_score: int | None
    priority: PriorityResult


def priority_input_for_sources(  # noqa: PLR0913 - explicit priority facts
    real_job_id: str,
    sources: list[JobPosting],
    *,
    representative_source_id: str | None,
    status: str,
    stage_a_score: int | None,
    stage_b_fit_score: int | None,
    stage_a_status: str | None = None,
    stage_b_status: str | None = None,
    input_facts_json: str | None = None,
    now: datetime,
) -> CanonicalPriorityInput | None:
    """Choose one inspectable source while keeping the parent ID separate.

    Args:
        real_job_id: Canonical parent identity.
        sources: Source postings linked to the parent.
        representative_source_id: Preferred source when none is evaluable.
        status: Canonical workflow status.
        stage_a_score: Current quick score, if any.
        stage_b_fit_score: Current detailed fit score, if any.
        stage_a_status: Quick evaluation state.
        stage_b_status: Detailed evaluation state.
        input_facts_json: Stored input and policy facts for the current score.
        now: Reference time for choosing an input.

    Returns:
        A single priority input, or None when no source can be shown.
    """
    if not sources:
        return None
    selected = select_real_job_input(real_job_id, sources, now=now)
    if selected is not None:
        job = selected.job
        source_id = selected.source_job_id
    else:
        source = next(
            (item for item in sources if item.id == representative_source_id),
            sources[0],
        )
        job = replace(source, discovered_at=min(item.discovered_at for item in sources))
        source_id = source.id
    if source_id is None:
        return None
    return CanonicalPriorityInput(
        real_job_id,
        source_id,
        job,
        status,
        stage_a_score,
        stage_b_fit_score,
        stage_a_status,
        stage_b_status,
        input_facts_json,
    )


def canonical_priority_rows(
    inputs: list[CanonicalPriorityInput], *, now: datetime
) -> list[CanonicalPriorityRow]:
    """Compute one priority per parent; caller can sort by tier and score.

    Args:
        inputs: Parent inputs, possibly with repeated parent IDs.
        now: Reference time for freshness scoring.

    Returns:
        Deduplicated priority rows keyed by real job ID.
    """
    unique = {item.real_job_id: item for item in inputs}
    return [
        CanonicalPriorityRow(
            real_job_id=item.real_job_id,
            source_job_id=item.source_job_id,
            job=item.job,
            status=item.status,
            stage_a_score=item.stage_a_score,
            priority=priority_for_job(
                item.job, evidence_fit=item.stage_b_fit_score, now=now
            ),
        )
        for item in unique.values()
    ]
