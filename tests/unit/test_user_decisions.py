"""User-facing decision semantics stay separate from workflow states."""

from __future__ import annotations

import pytest

from jobfeed.domain.user_decisions import (
    decision_for_status,
    statuses_for_decision,
)
from jobfeed.web.schemas.jobs_list import JobsListParams


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("new", "results"),
        ("scored", "results"),
        ("shortlisted", "wait"),
        ("awaiting_referral", "wait"),
        ("applied", "applied"),
        ("interviewing", "applied"),
        ("offer", "applied"),
        ("rejected", "applied"),
        ("ghosted", "applied"),
        ("ignored", "ignored"),
        ("archived", "ignored"),
    ],
)
def test_workflow_status_maps_to_one_user_decision_or_none(
    status: str, expected: str | None
) -> None:
    """Archived and ignored share the product's single Ignored decision."""
    assert decision_for_status(status) == expected


def test_decision_filters_have_exact_non_overlapping_statuses() -> None:
    """The public four-way filter cannot silently pull archived rows."""
    groups = {
        decision: statuses_for_decision(decision)
        for decision in ("results", "wait", "applied", "ignored")
    }

    assert groups["results"] == ("new", "scored")
    assert groups["wait"] == ("shortlisted", "awaiting_referral")
    assert groups["applied"] == (
        "applied",
        "interviewing",
        "offer",
        "rejected",
        "ghosted",
    )
    assert groups["ignored"] == ("ignored", "archived")
    flattened = [status for statuses in groups.values() for status in statuses]
    assert len(flattened) == len(set(flattened))


def test_jobs_api_translates_decision_without_exposing_status_lists() -> None:
    """The public wire owns the mapping instead of duplicating it in the UI."""
    query = JobsListParams(decision="ignored").to_query()
    assert query.statuses == ("ignored", "archived")
