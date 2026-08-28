"""Independent seniority gate boundary between the SDE gate and Stage A."""

from __future__ import annotations

from typing import Literal

from jobfeed.domain.models import JobPosting
from jobfeed.domain.seniority import SeniorityInput
from jobfeed.ports.seniority_gate import SeniorityGate

SeniorityGateMode = Literal["off", "shadow", "filter"]
_EXPLORATION_BUCKETS = 10


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
    survivors: list[JobPosting] = []
    blocked = 0
    for job, decision in zip(jobs, decisions, strict=True):
        if decision.result != "out_of_scope" or _is_exploration(job):
            survivors.append(job)
        else:
            blocked += 1
    return survivors, blocked


def _is_exploration(job: JobPosting) -> bool:
    job_id = _job_id(job)
    return job_id.isdigit() and int(job_id) % _EXPLORATION_BUCKETS == 0


def _job_id(job: JobPosting) -> str:
    if job.id is None:
        raise ValueError("seniority gate candidates must have a store id")
    return job.id


__all__ = ["SeniorityGateMode", "apply_seniority_gate"]
