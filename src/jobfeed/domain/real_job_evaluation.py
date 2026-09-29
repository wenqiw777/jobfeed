"""Choose one source posting as a real job's scoring input."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import cast

from jobfeed.domain.external_identity import observed_identifier
from jobfeed.domain.models import JobPosting, MLGateResult, QualityBand, StageBResult
from jobfeed.domain.real_job_identity import (
    compatible_role_facts,
    normalized_jd_body,
    strict_content_equivalent,
)


@dataclass(frozen=True)
class RealJobEvaluationInput:
    """The canonical owner and the source whose facts drive evaluation."""

    real_job_id: str
    source_job_id: str
    job: JobPosting
    input_revision: int = 1
    stage_a_score: int | None = None
    ml_gate_result: str | None = None
    claim_generation: int = 0


_QUALITY = {
    QualityBand.FULL: 0,
    QualityBand.GOOD: 1,
}
_ATS_PROVIDERS = frozenset(
    {"greenhouse", "ashby", "lever", "workday", "phenom", "eightfold"}
)
_MIN_COMPLETE_BODY_CHARS = 300
EVALUATION_ACTIVATION_KEY = "real_job_evaluation_activation_v1"


def select_real_job_input(
    real_job_id: str,
    sources: list[JobPosting],
    *,
    now: datetime,
) -> RealJobEvaluationInput | None:
    """Choose trusted official input before comparing lower-trust sources.

    Args:
        real_job_id: Canonical parent identifier.
        sources: Source postings attached to the parent.
        now: Evaluation time, reserved for the selection contract.

    Returns:
        The selected scoring input, or none when the group is ambiguous.
    """
    del now  # Closure is based on official evidence, not the time of evaluation.
    complete = [job for job in sources if job.jd_text and job.jd_quality in _QUALITY]
    if not complete or any(job.id is None for job in complete):
        return None
    complete = [job for job in complete if _is_ats(job)] or complete
    for index, left in enumerate(complete):
        if any(
            not strict_content_equivalent(left, right)
            for right in complete[index + 1 :]
        ):
            return None
    representative = min(
        complete,
        key=lambda job: (
            0 if _is_ats(job) else 1,
            _QUALITY[cast(QualityBand, job.jd_quality)],
            int(cast(str, job.id)),
        ),
    )
    first_discovery = min(job.discovered_at for job in sources)
    original_dates = [
        job.posted_at
        for job in sources
        if job.posted_at is not None and not job.is_repost
    ]
    official_closure = official_closed_at(sources)
    canonical_job = replace(
        representative,
        discovered_at=first_discovery,
        posted_at=min(original_dates) if original_dates else representative.posted_at,
        closed_at=official_closure,
        is_repost=all(job.is_repost is True for job in sources),
    )
    return RealJobEvaluationInput(
        real_job_id, cast(str, representative.id), canonical_job
    )


def canonical_sort_dates(sources: list[JobPosting]) -> tuple[datetime, datetime]:
    """Return the stable first-seen and sortable canonical posting dates.

    Args:
        sources: Source postings belonging to one canonical job.

    Returns:
        Canonical first-discovery and sortable posting timestamps.
    """
    first_discovery = min(job.discovered_at for job in sources)
    original_dates = [
        job.posted_at
        for job in sources
        if job.posted_at is not None and not job.is_repost
    ]
    original = min(original_dates) if original_dates else None
    canonical_posted = (
        original if original and original <= first_discovery else first_discovery
    )
    return first_discovery, canonical_posted


def official_closed_at(sources: list[JobPosting]) -> datetime | None:
    """Only an official ATS source can close the canonical posting.

    Args:
        sources: All source postings attached to the real job.

    Returns:
        Latest official closure time only when every official source is closed;
        otherwise none.
    """
    official = [job for job in sources if _is_ats(job)]
    if not official or any(job.closed_at is None for job in official):
        return None
    return max(job.closed_at for job in official if job.closed_at is not None)


def representative_source_id(real_job_id: str, sources: list[JobPosting]) -> int | None:
    """Prefer the canonical input, then the best official source available.

    Args:
        real_job_id: Parent identity used to select the scoring input.
        sources: Attached source postings with their store IDs.

    Returns:
        Selected source ID, or none when the parent has no source rows.
    """
    selected = select_real_job_input(real_job_id, sources, now=datetime.now(UTC))
    if selected is not None:
        return int(selected.source_job_id)
    available = [job for job in sources if job.id is not None]
    if not available:
        return None
    return int(
        cast(
            str,
            min(
                available,
                key=lambda job: (
                    not _is_ats(job),
                    _QUALITY.get(cast(QualityBand, job.jd_quality), len(_QUALITY)),
                    int(cast(str, job.id)),
                ),
            ).id,
        )
    )


def conflicting_complete_sources(
    sources: list[JobPosting],
) -> tuple[int, int] | None:
    """Find divergent full descriptions for the same role.

    Time complexity: O(n²) for n complete source descriptions.

    Args:
        sources: Source postings under one real job.

    Returns:
        The two source IDs requiring review, or None when descriptions agree.
    """
    complete = [
        job
        for job in sources
        if job.id is not None
        and job.jd_text
        and job.jd_quality in _QUALITY
        and len(normalized_jd_body(job.jd_text)) >= _MIN_COMPLETE_BODY_CHARS
    ]
    complete = [job for job in complete if _is_ats(job)] or complete
    for index, left in enumerate(complete):
        for right in complete[index + 1 :]:
            if compatible_role_facts(left, right) and not strict_content_equivalent(
                left, right
            ):
                return int(cast(str, left.id)), int(cast(str, right.id))
    return None


def legacy_evaluation_input_hold(
    selected: RealJobEvaluationInput | None,
    sources: list[JobPosting],
    evaluated_sources: list[tuple[int, datetime | None]],
) -> str | None:
    """Explain why an old paid score cannot be tied to the current JD.

    Args:
        selected: Current canonical input, if one can be selected.
        sources: Current source postings for the parent.
        evaluated_sources: Source IDs and times of completed old scores.

    Returns:
        A hold state when reuse is unproven, otherwise none.
    """
    by_id = {int(job.id): job for job in sources if job.id is not None}
    for source_id, _ in evaluated_sources:
        job = by_id.get(source_id)
        if job is None or not job.jd_text or job.jd_quality not in _QUALITY:
            return "evaluation_input_missing"
    if selected is None:
        return (
            "evaluation_input_conflict"
            if any(job.jd_text and job.jd_quality in _QUALITY for job in sources)
            else "evaluation_input_missing"
        )
    # Historical scores are explicitly reusable; timestamps do not prove a
    # material input change. Current input selection already resolves trust.
    return None


def _is_ats(job: JobPosting) -> bool:
    identifier = observed_identifier(job.url)
    return identifier is not None and identifier.provider in _ATS_PROVIDERS


def input_facts_json(
    value: RealJobEvaluationInput,
    *,
    stage_a_policy: dict[str, object] | None = None,
    stage_b_policy: dict[str, object] | None = None,
) -> str:
    """Serialize the scoring facts for direct revision comparison.

    Args:
        value: Selected canonical evaluation input.
        stage_a_policy: Explicit quick-score model and gate policy, if known.
        stage_b_policy: Explicit detailed-score policy, if known.

    Returns:
        Stable, readable JSON for title, company, location, and posting time.
    """
    job = value.job
    facts: dict[str, object] = {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
    }
    if stage_a_policy is not None:
        facts["stage_a_policy"] = stage_a_policy
    if stage_b_policy is not None:
        facts["stage_b_policy"] = stage_b_policy
    return json.dumps(
        facts,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def same_evaluation_input(
    stored_jd_text: str,
    stored_facts_json: str,
    selected: RealJobEvaluationInput,
    *,
    stage_a_policy: dict[str, object] | None = None,
) -> bool:
    """Compare stored and selected scoring input, ignoring JD layout only.

    Args:
        stored_jd_text: JD saved with the current canonical evaluation.
        stored_facts_json: Serialized facts saved with that evaluation.
        selected: Newly selected canonical evaluation input.
        stage_a_policy: Requested quick-score policy, when checking a claim.

    Returns:
        Whether the substantive JD and scoring facts are unchanged.
    """
    stored = json.loads(stored_facts_json)
    stored_a_policy = stored.pop("stage_a_policy", None)
    stored.pop("stage_b_policy", None)
    expected = json.loads(input_facts_json(selected))
    return (
        stored == expected
        and (
            stage_a_policy is None
            or stored_a_policy is None
            or stored_a_policy == stage_a_policy
        )
        and normalized_jd_body(stored_jd_text)
        == normalized_jd_body(selected.job.jd_text)
    )


def replace_evaluation_policies(
    stored_facts_json: str,
    *,
    stage_a_policy: dict[str, object] | None = None,
    stage_b_policy: dict[str, object] | None = None,
) -> str:
    """Update only explicit model/policy facts without changing source facts.

    Args:
        stored_facts_json: Current readable scoring facts.
        stage_a_policy: Replacement quick-score policy, when supplied.
        stage_b_policy: Replacement detailed-score policy, when supplied.

    Returns:
        Readable JSON with the requested policy fields replaced.
    """
    facts = json.loads(stored_facts_json)
    if stage_a_policy is not None:
        facts["stage_a_policy"] = stage_a_policy
    if stage_b_policy is not None:
        facts["stage_b_policy"] = stage_b_policy
    return json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def policy_visibility(
    stored_facts_json: str | None,
    *,
    stage_a_policy: dict[str, object] | None,
    stage_b_policy: dict[str, object] | None,
) -> tuple[bool, bool, str | None]:
    """Classify current scores against the configured scoring policy.

    Args:
        stored_facts_json: Readable input facts saved with the current score.
        stage_a_policy: Current quick-score policy, or None for legacy reads.
        stage_b_policy: Current detailed-score policy, or None for legacy reads.

    Returns:
        Quick visibility, detailed visibility, and a pending reason code.
    """
    if stage_a_policy is None and stage_b_policy is None:
        return True, True, None
    facts = json.loads(stored_facts_json or "{}")
    stored_a = facts.get("stage_a_policy")
    stored_b = facts.get("stage_b_policy")
    # Missing historical policy records do not invalidate an existing score.
    if (
        stored_a is not None
        and stage_a_policy is not None
        and stored_a != stage_a_policy
    ):
        return False, False, "stage_a_policy_changed"
    if (
        stored_b is not None
        and stage_b_policy is not None
        and stored_b != stage_b_policy
    ):
        return True, False, "stage_b_policy_changed"
    return True, True, None


def mask_stale_evaluation_row(
    row: dict[str, object],
    *,
    stage_a_policy: dict[str, object] | None,
    stage_b_policy: dict[str, object] | None,
) -> None:
    """Hide unverified current scores while exposing the pending reason.

    Args:
        row: Mutable canonical view row containing evaluation fields.
        stage_a_policy: Current quick-score policy.
        stage_b_policy: Current detailed-score policy.
    """
    stage_a_current, stage_b_current, reason = policy_visibility(
        cast(str | None, row.get("eval_input_facts_json")),
        stage_a_policy=stage_a_policy,
        stage_b_policy=stage_b_policy,
    )
    has_score = (
        row.get("stage_a_score") is not None or row.get("stage_b_verdict") is not None
    )
    row["evaluation_stale_reason"] = reason if has_score else None
    row["stale_stage_a_score"] = (
        row.get("stage_a_score") if has_score and not stage_a_current else None
    )
    if not stage_a_current:
        row["stage_a_score"] = None
        row["stage_a_one_line"] = None
    if not stage_b_current:
        row["stage_b_status"] = None
        row["stage_b_verdict"] = None
        row["stage_b_json"] = None
    row.pop("eval_input_facts_json", None)


def stage_b_result_json(result: StageBResult) -> str:
    """Serialize the complete current Stage B answer.

    Args:
        result: Stage B decision and supporting evidence.

    Returns:
        Readable JSON for the canonical evaluation row.
    """
    return json.dumps(
        {
            "verdict": result.verdict.value,
            "jd_summary": result.jd_summary,
            "fit_analysis": asdict(result.fit_analysis),
            "resume_hooks": result.resume_hooks,
            "raw_blocks": result.raw_blocks,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def ml_gate_result_json(result: MLGateResult) -> str:
    """Serialize the gate's structured evidence.

    Args:
        result: Gate decision and feature evidence.

    Returns:
        Readable JSON for the canonical evaluation row.
    """
    return json.dumps(
        asdict(result), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
