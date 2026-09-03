"""Typed persistence boundary for fenced pipeline-run leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from jobfeed.domain.models import PipelineRun

RunKind = Literal["scan", "evaluate"]


@dataclass(frozen=True)
class RecoveredRun:
    """One running attempt changed to interrupted during lease recovery."""

    run_id: str
    kind: RunKind
    source: str
    restart_count: int


@runtime_checkable
class RunLeaseStore(Protocol):
    """Atomic lease operations required by scan/evaluate orchestration."""

    async def start_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        now: datetime,
    ) -> int | None:
        """Atomically acquire ``kind`` and insert its running history row.

        Args:
            run: New running pipeline row to insert in the same transaction.
            kind: Exclusive pipeline kind.
            owner_id: Canonical UUID text for the worker process.
            now: Aware UTC acquisition timestamp.

        Returns:
            Positive fencing generation, or ``None`` when an unexpired owner
            already holds the run kind.
        """
        ...

    async def renew_run_lease(
        self,
        *,
        kind: RunKind,
        owner_id: str,
        run_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Renew only the exact, unexpired fencing token.

        Args:
            kind: Exclusive pipeline kind.
            owner_id: Worker identity from acquisition.
            run_id: Pipeline identity from acquisition.
            generation: Positive fencing generation from acquisition.
            now: Aware UTC renewal timestamp.

        Returns:
            True only when the exact unexpired token was renewed.
        """
        ...

    async def checkpoint_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Persist current counters only while the exact lease is live.

        Args:
            run: Current pipeline snapshot.
            kind: Exclusive pipeline kind.
            owner_id: Worker identity from acquisition.
            generation: Fencing generation from acquisition.
            now: Aware UTC checkpoint timestamp.

        Returns:
            True only when the exact live token saved the snapshot.
        """
        ...

    async def finalize_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Persist a terminal run and release only its exact fencing token.

        Args:
            run: Terminal pipeline run snapshot.
            kind: Exclusive pipeline kind.
            owner_id: Worker identity from acquisition.
            generation: Positive fencing generation from acquisition.
            now: Aware UTC finalization timestamp.

        Returns:
            True only when the run and exact lease were finalized.
        """
        ...


@runtime_checkable
class RecoverableRunLeaseStore(Protocol):
    """Optional lifecycle controls exposed by stores with durable run leases."""

    async def recover_expired_run_leases(self, *, now: datetime) -> list[RecoveredRun]:
        """Fail expired running rows and release their leases.

        Args:
            now: Aware UTC recovery timestamp.

        Returns:
            Newly interrupted attempts eligible for manager policy handling.
        """
        ...

    async def link_restarted_run(self, run_id: str, replacement_run_id: str) -> bool:
        """Link an interrupted attempt to its one automatic replacement.

        Args:
            run_id: Interrupted attempt identity.
            replacement_run_id: Automatically started replacement identity.

        Returns:
            True only when the previously unlinked attempt was updated.
        """
        ...

    async def stop_pipeline_run(self, run_id: str, *, now: datetime) -> bool:
        """Fail one running row and release its matching lease.

        Args:
            run_id: Pipeline run identity to stop.
            now: Aware UTC stop timestamp.

        Returns:
            True when a running row and its lease were stopped.
        """
        ...


__all__ = ["RecoverableRunLeaseStore", "RecoveredRun", "RunKind", "RunLeaseStore"]
