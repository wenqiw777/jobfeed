"""Independent seniority gate boundary between the SDE gate and Stage A."""

from __future__ import annotations

import hashlib
from typing import Literal

from jobfeed.domain.models import JobPosting
from jobfeed.domain.seniority import SeniorityDecision, SeniorityInput
from jobfeed.ports.seniority_gate import SeniorityGate

SeniorityGateMode = Literal["off", "shadow", "filter"]
_MODEL_EXPLORATION_PERCENT = 5
_MODEL_EXPLORATION_MAX_CONFIDENCE = 0.95
_MODEL_EXPLORATION_LIMIT = 10


async def apply_seniority_gate(
    gate: SeniorityGate,
    jobs: list[JobPosting],
    *,
    mode: SeniorityGateMode,
) -> tuple[list[JobPosting], int]:
    """Score candidates and optionally block out-of-scope seniority roles.

    Args:
        gate: Independent rule-plus-model seniority gate.
        jobs: SDE-gate survivors in evaluation order.
        mode: Off, shadow-only, or active filtering.

    Returns:
        Surviving jobs and the number actively blocked.

    Raises:
        ValueError: If the gate returns a result count different from its input.
    """
    if not jobs or mode == "off":
        return jobs, 0
    decisions = await gate.predict_batch(
        [
            SeniorityInput(
                job_id=_job_id(job),
                title=job.title,
                jd_text=job.jd_text or "",
            )
            for job in jobs
        ]
    )
    if len(decisions) != len(jobs):
        raise ValueError("seniority gate returned the wrong number of decisions")
    if mode == "shadow":
        return jobs, 0
    exploration_indexes = _model_exploration_indexes(jobs, decisions)
    survivors: list[JobPosting] = []
    blocked = 0
    for index, (job, decision) in enumerate(zip(jobs, decisions, strict=True)):
        if decision.result != "out_of_scope" or index in exploration_indexes:
            survivors.append(job)
        else:
            blocked += 1
    return survivors, blocked


def _model_exploration_indexes(
    jobs: list[JobPosting], decisions: list[SeniorityDecision]
) -> set[int]:
    """Select a bounded, order-independent sample of uncertain model blocks."""
    candidates: list[tuple[bytes, int]] = []
    for index, (job, decision) in enumerate(zip(jobs, decisions, strict=True)):
        if (
            decision.result != "out_of_scope"
            or decision.source != "model"
            or decision.confidence >= _MODEL_EXPLORATION_MAX_CONFIDENCE
        ):
            continue
        digest = _exploration_digest(job, decision.version)
        bucket = int.from_bytes(digest[:8], "big") % 100
        if bucket < _MODEL_EXPLORATION_PERCENT:
            candidates.append((digest, index))
    candidates.sort()
    return {index for _digest, index in candidates[:_MODEL_EXPLORATION_LIMIT]}


def _exploration_digest(job: JobPosting, model_version: str) -> bytes:
    """Hash durable job identity so database IDs do not bias the sample."""
    identity = f"{job.platform}:{job.canonical_id}:{model_version}".encode()
    return hashlib.sha256(identity, usedforsecurity=False).digest()


def _job_id(job: JobPosting) -> str:
    if job.id is None:
        raise ValueError("seniority gate candidates must have a store id")
    return job.id


__all__ = ["SeniorityGateMode", "apply_seniority_gate"]
