"""Transition-only run lease adapter for the pre-cutover PostgreSQL store."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from jobfeed.domain.models import PipelineRun
from jobfeed.ports.run_leases import RunKind


class _LegacyRunStore(Protocol):
    async def record_pipeline_run(self, run: PipelineRun) -> None: ...

    async def update_pipeline_run_status(self, run: PipelineRun) -> None: ...


class LegacyRunLeaseStore:
    """Keep PG runtime usable until its final replacement by SQLite.

    This adapter intentionally offers no cross-process fencing. It exists only
    while ordinary runtime configuration still selects the legacy PostgreSQL
    store and is deleted with that wiring at cutover.
    """

    def __init__(self, store: _LegacyRunStore) -> None:
        """Wrap the two legacy pipeline-run persistence operations."""
        self._store = store

    async def start_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        now: datetime,
    ) -> int:
        """Persist the legacy running row and return a transition token.

        Args:
            run: Running pipeline snapshot.
            kind: Transition-only run kind.
            owner_id: Unused SQLite-era owner identity.
            now: Unused acquisition timestamp.

        Returns:
            The sole transition generation.
        """
        del kind, owner_id, now
        await self._store.record_pipeline_run(run)
        return 1

    async def renew_run_lease(
        self,
        *,
        kind: RunKind,
        owner_id: str,
        run_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Keep the transition token active while PostgreSQL remains wired.

        Args:
            kind: Transition-only run kind.
            owner_id: Unused SQLite-era owner identity.
            run_id: Unused pipeline identity.
            generation: Unused transition generation.
            now: Unused renewal timestamp.

        Returns:
            Always true during the bounded transition window.
        """
        del kind, owner_id, run_id, generation, now
        return True

    async def finalize_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Persist the legacy terminal row during the transition window.

        Args:
            run: Terminal pipeline snapshot.
            kind: Transition-only run kind.
            owner_id: Unused SQLite-era owner identity.
            generation: Unused transition generation.
            now: Unused finalization timestamp.

        Returns:
            True after the legacy terminal write succeeds.
        """
        del kind, owner_id, generation, now
        await self._store.update_pipeline_run_status(run)
        return True


__all__ = ["LegacyRunLeaseStore"]
