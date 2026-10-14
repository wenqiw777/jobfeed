"""Apply persisted human decisions before automatic posting screening."""

from __future__ import annotations

import json
from typing import Protocol, cast

from jobfeed.domain.models import JobPosting
from jobfeed.domain.posting_spam import assess_posting, company_key


class _StateStore(Protocol):
    async def get_state(self, key: str) -> str | None: ...


async def spam_block_reason(store: object, job: JobPosting) -> str | None:
    """Apply human decisions before automatic screening.

    Args:
        store: State-reading store, or a stateless test adapter.
        job: Candidate posting used for scoring.

    Returns:
        Exclusion reason, or None for clear, uncertain, or kept postings.
    """
    employer = decision = saved = None
    if callable(getattr(store, "get_state", None)):
        state = cast(_StateStore, store)
        employer = await state.get_state("spam:company:" + company_key(job.company))
        if job.id:
            decision = await state.get_state("spam:decision:" + job.id)
            saved = await state.get_state("spam:job:" + job.id)
    if employer == "block":
        return "spam:company_block"
    if decision == "allow":
        return None
    if decision == "block":
        return "spam:job_block"
    if employer == "allow":
        return None
    if employer == "auto_block":
        return "spam:repeated_publisher"
    assessment = (
        json.loads(saved) if isinstance(saved, str) else assess_posting(job).to_dict()
    )
    return (
        "spam:" + str(assessment["category"])
        if assessment["disposition"] == "block"
        else None
    )
