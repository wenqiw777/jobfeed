"""Compose the Task 2 SQLite core capabilities behind one store facade."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from jobfeed.adapters.store._sqlite_runs import _get_pipeline_run
from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_jobs_evaluations import SqliteJobsEvaluations
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_ops import SqliteOps
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.adapters.store.sqlite_status_applications import (
    SqliteStatusApplications,
)
from jobfeed.adapters.store.sqlite_unified_evaluations import (
    SqliteUnifiedEvaluations,
)
from jobfeed.adapters.store.sqlite_views_performance import (
    SqliteViewsPerformance,
)
from jobfeed.domain.models import PipelineRun

Clock = Callable[[], datetime]


class SQLiteStore(
    SqliteJobsEvaluations,
    SqliteUnifiedEvaluations,
    SqliteClaimsRuns,
    SqliteStatusApplications,
    SqliteOps,
    SqliteViewsPerformance,
):
    """Own one lifecycle and compose the complete typed SQLite runtime."""

    def __init__(self, path: Path, *, clock: Clock | None = None) -> None:
        """Create a closed store for one database file.

        Args:
            path: SQLite database file path.
            clock: Application UTC clock used by claims and lease recovery.
        """
        self._lifecycle = SqliteLifecycle(path, ensure_sqlite_schema)
        self._application_clock = clock or _utc_now

    async def connect(self) -> None:
        """Open the schema and recover only expired occupied run leases.

        Raises:
            Exception: Propagates lifecycle, schema, or lease-recovery failures
                after closing any partially opened lifecycle.
        """
        if self._lifecycle.is_open:
            return
        await self._lifecycle.open()
        try:
            await self.recover_expired_run_leases(now=self._now())
        except BaseException:
            await self._lifecycle.close()
            raise

    async def close(self) -> None:
        """Close the shared SQLite lifecycle idempotently."""
        await self._lifecycle.close()

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load one persisted pipeline run by identity.

        Args:
            run_id: Exact pipeline UUID text.

        Returns:
            Hydrated run when present, otherwise None.
        """
        async with self._lifecycle.connection() as connection:
            return await _get_pipeline_run(connection, run_id)

    def _now(self) -> datetime:
        value = self._application_clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SQLite store clock must return an aware datetime")
        return value.astimezone(UTC)

    def _claim_time(self, value: datetime | None) -> datetime:
        return self._now() if value is None else super()._claim_time(value)

    def _application_time(self, value: datetime | None = None) -> datetime:
        return self._now() if value is None else super()._application_time(value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["SQLiteStore"]
