"""Shared fixtures for SQLite jobs and evaluation capability contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jobfeed.adapters.store.sqlite_jobs_evaluations import SqliteJobsEvaluations
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    JobPosting,
    MatchItem,
    QualityBand,
    StageAResult,
    StageBResult,
    Verdict,
)

FIXED_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


async def open_sqlite_store(
    database_path: Path,
) -> tuple[SqliteLifecycle, SqliteJobsEvaluations]:
    """Open a v1 lifecycle and its jobs/evaluations capability.

    Args: database file path.
    Returns: open lifecycle and bound capability.
    """
    lifecycle = SqliteLifecycle(database_path, ensure_sqlite_schema)
    await lifecycle.open()
    return lifecycle, SqliteJobsEvaluations(lifecycle)


def make_job(  # noqa: PLR0913
    canonical_id: str,
    *,
    discovered_at: datetime = FIXED_NOW,
    title: str = "Backend Engineer",
    jd_text: str | None = "complete job description",
    jd_quality: QualityBand | None = QualityBand.FULL,
    closed_at: datetime | None = None,
    enrich_error: str | None = None,
) -> JobPosting:
    """Build a deterministic job posting for adapter contracts.

    Args: natural key plus optional time, content, quality, and closure fields.
    Returns: domain posting fixture.
    """
    return JobPosting(
        platform="mock",
        canonical_id=canonical_id,
        url=f"https://example.test/{canonical_id}",
        title=title,
        company="Example Corp",
        location="New York, NY",
        discovered_at=discovered_at,
        jd_text=jd_text,
        jd_quality=jd_quality,
        enriched_at=discovered_at if jd_text else None,
        enrich_source="fixture" if jd_text else None,
        closed_at=closed_at,
        enrich_error=enrich_error,
    )


def stage_a(score: int = 80) -> StageAResult:
    """Build a deterministic Stage A result.

    Args: validated Stage A score.
    Returns: domain Stage A result fixture.
    """
    return StageAResult(
        score=score,
        one_line="Strong match",
        timing_eligible="yes",
        model="stage-a-model",
        prompt_hash="prompt-a",
        resume_hash="resume-a",
        cost_usd=0.01,
    )


def stage_b(*, raw_blocks: dict[str, object] | None = None) -> StageBResult:
    """Build a deterministic Stage B result.

    Args: optional raw structured blocks.
    Returns: domain Stage B result fixture.
    """
    return StageBResult(
        verdict=Verdict.CONSIDER,
        jd_summary="Build reliable systems",
        fit_analysis=FitAnalysis(
            score=88,
            strengths=[MatchItem(requirement="Python", evidence="Seven years")],
            gaps=[
                GapItem(
                    requirement="Rust",
                    severity="minor",
                    mitigation="Mention systems experience",
                )
            ],
        ),
        resume_hooks=["Distributed systems", "Ownership"],
        model="stage-b-model",
        prompt_hash="prompt-b",
        resume_hash="resume-b",
        cost_usd=0.05,
        raw_blocks=raw_blocks,
    )
