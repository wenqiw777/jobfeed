"""Canonical jobs-list API mapping contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.domain.models import JobPosting
from jobfeed.domain.models_views import JobsViewPage, JobsViewRow
from jobfeed.web.schemas.jobs_list import jobs_list_response

_MATCH_SCORE = 20


def test_jobs_list_exposes_only_unified_evaluation_summary() -> None:
    row = JobsViewRow(
        job=JobPosting(
            id="1",
            platform="test",
            canonical_id="canonical",
            url="https://example.test/1",
            title="Engineer",
            company="Example",
            location="Remote",
            discovered_at=datetime(2026, 8, 25, tzinfo=UTC),
        ),
        company_norm="example",
        title_norm="engineer",
        status="scored",
        evaluation_score=_MATCH_SCORE,
        evaluation_verdict="weak_match",
        evaluation_status="completed",
        evaluator_version="unified-v2",
    )

    payload = jobs_list_response(
        JobsViewPage(rows=[row], total=1, tab_counts={})
    ).model_dump()

    summary = payload["jobs"][0]
    assert summary["evaluation_score"] == _MATCH_SCORE
    assert summary["evaluation_verdict"] == "weak_match"
    assert summary["evaluation_status"] == "completed"
    assert summary["evaluator_version"] == "unified-v2"
    assert "stage_a_score" not in summary
    assert "stage_b_fit_score" not in summary
