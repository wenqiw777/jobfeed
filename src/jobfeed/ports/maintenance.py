"""Narrow store capability for stale-job maintenance commands."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StaleJobMaintenance(Protocol):
    """Count or close stale jobs without requiring unrelated store operations."""

    async def mark_stale_jobs_closed(
        self,
        *,
        older_than_days: int,
        dry_run: bool,
    ) -> int:
        """Count or close stale jobs without a usable job description.

        Args:
            older_than_days: Discovery-age threshold in whole days.
            dry_run: Count without writing when true.

        Returns:
            Number of matching or updated jobs.
        """
        ...


__all__ = ["StaleJobMaintenance"]
