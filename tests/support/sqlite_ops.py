"""Shared fixtures for SQLite operational capability contracts."""

from __future__ import annotations

from pathlib import Path

from jobfeed.adapters.store.sqlite_jobs_evaluations import SqliteJobsEvaluations
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_ops import SqliteOps
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema


async def open_sqlite_ops(
    database_path: Path,
) -> tuple[SqliteLifecycle, SqliteOps, SqliteJobsEvaluations]:
    """Open one lifecycle shared by ops and core test capabilities.

    Args: database file path.
    Returns: open lifecycle, ops capability, and job seeding capability.
    """
    lifecycle = SqliteLifecycle(database_path, ensure_sqlite_schema)
    await lifecycle.open()
    return lifecycle, SqliteOps(lifecycle), SqliteJobsEvaluations(lifecycle)
