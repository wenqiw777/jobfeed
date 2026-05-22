"""Unit tests for Markdown digest rendering."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.digest import render_digest
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    JobEvaluation,
    MatchItem,
    StageAResult,
    StageBResult,
    Verdict,
)
from tests.support.factories import make_job

APPLY_SCORE = 91
CONSIDER_SCORE = 72
SKIP_SCORE = 30
TOTAL_JOBS = 3


def make_stage_a(score: int) -> StageAResult:
    """Create Stage A result for digest tests.

    Args:
        score: Stage A score.

    Returns:
        Stage A result with deterministic metadata.
    """
    return StageAResult(
        score=score,
        one_line="Good fit for automation work",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="prompt",
        resume_hash="resume",
    )


def make_stage_b(verdict: Verdict, score: int) -> StageBResult:
    """Create Stage B result for digest tests.

    Args:
        verdict: Application recommendation.
        score: Detailed fit score.

    Returns:
        Stage B result with strengths and gaps.
    """
    return StageBResult(
        verdict=verdict,
        jd_summary="Builds Python automation for job workflows.",
        fit_analysis=FitAnalysis(
            score=score,
            strengths=[
                MatchItem(requirement="Python", evidence="Built Python services")
            ],
            gaps=[
                GapItem(
                    requirement="Kubernetes",
                    severity="minor",
                    mitigation="Mention Docker deployment work",
                )
            ],
        ),
        resume_hooks=["Mention local-first automation"],
        model="mock/stage-b",
        prompt_hash="prompt",
        resume_hash="resume",
    )


def make_evaluation(
    canonical_id: str,
    verdict: Verdict | None,
    score: int,
    discovered_at: datetime,
) -> JobEvaluation:
    """Create an evaluation for digest tests.

    Args:
        canonical_id: Stable source identity.
        verdict: Optional Stage B verdict.
        score: Score used by Stage A and Stage B.
        discovered_at: Discovery timestamp.

    Returns:
        Job evaluation with optional Stage B result.
    """
    return JobEvaluation(
        job=make_job(
            canonical_id,
            title="Backend Intern",
            company="Example",
            discovered_at=discovered_at,
        ),
        stage_a=make_stage_a(score),
        stage_b=make_stage_b(verdict, score) if verdict is not None else None,
    )


def test_render_digest_contains_header_date_and_apply_details() -> None:
    """Apply jobs should show score, job identity, URL, strengths, and gaps."""
    now = datetime.now(UTC)
    digest = render_digest(
        [make_evaluation("apply", Verdict.APPLY, APPLY_SCORE, now)],
        {"total_jobs": 1},
    )

    assert "# Daily Digest" in digest
    assert now.date().isoformat() in digest
    assert f"**{APPLY_SCORE}** Backend Intern @ Example" in digest
    assert "https://example.com/apply" in digest
    assert "Python" in digest
    assert "Kubernetes" in digest


def test_render_digest_consider_jobs_are_one_liners() -> None:
    """Consider jobs should render compact one-line entries."""
    digest = render_digest(
        [
            make_evaluation(
                "consider",
                Verdict.CONSIDER,
                CONSIDER_SCORE,
                datetime.now(UTC),
            )
        ],
        {},
    )

    assert "## Consider" in digest
    assert f"**{CONSIDER_SCORE}** Backend Intern @ Example" in digest
    assert "Good fit for automation work" in digest


def test_render_digest_skip_section_shows_count() -> None:
    """Skip tier should summarize skipped jobs by count."""
    digest = render_digest(
        [
            make_evaluation("skip", Verdict.SKIP, SKIP_SCORE, datetime.now(UTC)),
            make_evaluation("missing-stage-b", None, SKIP_SCORE, datetime.now(UTC)),
        ],
        {},
    )

    assert "(2 jobs skipped)" in digest


def test_render_digest_cutoff_splits_apply_jobs() -> None:
    """cutoff_at should split apply jobs into new and previously seen groups."""
    cutoff = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    new_time = datetime(2026, 5, 21, 13, 0, tzinfo=UTC)
    old_time = datetime(2026, 5, 21, 11, 0, tzinfo=UTC)

    digest = render_digest(
        [
            make_evaluation("new", Verdict.APPLY, APPLY_SCORE, new_time),
            make_evaluation("old", Verdict.APPLY, APPLY_SCORE, old_time),
        ],
        {},
        cutoff_at=cutoff,
    )

    assert "### New" in digest
    assert "https://example.com/new" in digest
    assert "### Previously seen" in digest
    assert "https://example.com/old" in digest


def test_render_digest_rejects_timezone_naive_cutoff() -> None:
    """cutoff_at should be timezone-aware to avoid datetime comparison errors."""
    with pytest.raises(ValueError, match="cutoff_at must be timezone-aware"):
        render_digest(
            [
                make_evaluation(
                    "apply",
                    Verdict.APPLY,
                    APPLY_SCORE,
                    datetime.now(UTC),
                )
            ],
            {},
            cutoff_at=datetime(2026, 5, 21, 12, 0),
        )


def test_render_digest_rejects_timezone_naive_discovery_time() -> None:
    """cutoff splits should require timezone-aware discovery timestamps."""
    cutoff = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)

    with pytest.raises(
        ValueError,
        match=r"job\.discovered_at must be timezone-aware",
    ):
        render_digest(
            [
                make_evaluation(
                    "naive",
                    Verdict.APPLY,
                    APPLY_SCORE,
                    datetime(2026, 5, 21, 13, 0),
                )
            ],
            {},
            cutoff_at=cutoff,
        )


def test_render_digest_empty_input_is_valid() -> None:
    """Empty evaluations should still produce all digest sections."""
    digest = render_digest([], {})

    assert "# Daily Digest" in digest
    assert "## Apply" in digest
    assert "## Consider" in digest
    assert "(0 jobs skipped)" in digest
    assert "total_jobs: 0" in digest


def test_render_digest_stats_summary_uses_provided_counts() -> None:
    """Stats section should render known counters from the stats dict."""
    digest = render_digest(
        [],
        {
            "total_jobs": TOTAL_JOBS,
            "scraped_today": TOTAL_JOBS,
            "llm_calls_today": TOTAL_JOBS,
        },
    )

    assert f"total_jobs: {TOTAL_JOBS}" in digest
    assert f"scraped_today: {TOTAL_JOBS}" in digest
    assert f"llm_calls_today: {TOTAL_JOBS}" in digest
