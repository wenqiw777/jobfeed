"""Shared deterministic fixtures for SQLite status and application tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jobfeed.adapters.store.sqlite_jobs_evaluations import SqliteJobsEvaluations
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.models import JobPosting

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


async def _open_lifecycle(tmp_path: Path) -> SqliteLifecycle:
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    return lifecycle


async def _seed_job(
    lifecycle: SqliteLifecycle,
    canonical_id: str,
    *,
    company: str = "Example",
    title: str = "Software Engineer",
) -> str:
    jobs = SqliteJobsEvaluations(lifecycle)
    saved = await jobs.save_job(
        JobPosting(
            platform="test",
            canonical_id=canonical_id,
            url=f"https://example.test/{canonical_id}",
            title=title,
            company=company,
            location="Remote",
            discovered_at=NOW,
        )
    )
    return saved.job_id
