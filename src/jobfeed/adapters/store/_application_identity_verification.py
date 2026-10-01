"""Recheck resolver verification facts against the current source transaction."""

from __future__ import annotations

import json

from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.real_job_identity import normalized_jd_body


def verification_facts_match(posting: JobPosting, state_value: str) -> bool:
    """Keep legacy receipts valid; reject stale or malformed verification facts.

    Args:
        posting: Current source read inside the evidence-write transaction.
        state_value: Serialized outcome, optionally including verification facts.

    Returns:
        Whether the current normalized source facts match the verified snapshot.
    """
    try:
        receipt = json.loads(state_value)
    except (TypeError, ValueError):
        return True
    if not isinstance(receipt, dict) or "verification_facts" not in receipt:
        return True
    facts = receipt["verification_facts"]
    if not isinstance(facts, dict):
        return False
    return bool(
        facts
        == {
            "company": normalize_company(posting.company),
            "title": normalize(posting.title),
            "jd_body": normalized_jd_body(posting.jd_text),
        }
    )
