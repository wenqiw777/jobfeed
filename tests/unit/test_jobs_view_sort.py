"""Canonical unified Triage ranking contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobfeed.domain.models import JobPosting
from jobfeed.domain.models_views import JobsViewRow
from jobfeed.services._jobs_view_sort import verdict_group_sort_key

_NOW = datetime.now(UTC)


def _row(
    id: str,
    tier: str | None,
    score: int | None,
    *,
    posted_days_ago: int = 0,
) -> JobsViewRow:
    return JobsViewRow(
        job=JobPosting(
            id=id,
            platform="test",
            canonical_id=id,
            url=f"https://example.test/{id}",
            title="Engineer",
            company="Example",
            location="Remote",
            discovered_at=_NOW,
            posted_at=_NOW - timedelta(days=posted_days_ago),
        ),
        company_norm="example",
        title_norm="engineer",
        status="scored",
        evaluation_score=score,
        evaluation_verdict=tier,
        evaluation_status="completed" if score is not None else None,
        evaluator_version="unified-v1" if score is not None else None,
    )


def test_triage_uses_hard_unified_tier_order() -> None:
    rows = [
        _row("5", None, None),
        _row("4", "ineligible", 100),
        _row("3", "weak_match", 100),
        _row("2", "possible_match", 1),
        _row("1", "strong_match", 1),
    ]

    ordered = sorted(rows, key=verdict_group_sort_key)

    assert [row.job.id for row in ordered] == ["1", "2", "3", "4", "5"]


def test_freshness_can_break_close_score_inside_one_unified_tier() -> None:
    old_higher = _row("1", "strong_match", 90, posted_days_ago=14)
    fresh_lower = _row("2", "strong_match", 88)

    ordered = sorted([old_higher, fresh_lower], key=verdict_group_sort_key)

    assert [row.job.id for row in ordered] == ["2", "1"]
