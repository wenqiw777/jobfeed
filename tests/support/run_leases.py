"""Reusable successful run-lease fake for service-focused unit tests."""

from __future__ import annotations

from datetime import datetime

from jobfeed.domain.models import PipelineRun
from jobfeed.ports.run_leases import RunKind


class SuccessfulRunLeaseMixin:
    """Provide a stable fencing token when lease behavior is not under test."""

    async def start_run_with_lease(
        self,
        _run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        now: datetime,
    ) -> int:
        """Return one successful generation.

        Args:
            _run: Running row ignored by this behavior-neutral fake.
            kind: Lease kind under test.
            owner_id: Worker identity under test.
            now: Acquisition time under test.

        Returns:
            Stable positive generation.
        """
        del kind, owner_id, now
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
        """Keep the fake token active.

        Args:
            kind: Lease kind under test.
            owner_id: Worker identity under test.
            run_id: Pipeline identity under test.
            generation: Fencing generation under test.
            now: Renewal time under test.

        Returns:
            Always true for service tests unrelated to lease loss.
        """
        del kind, owner_id, run_id, generation, now
        return True

    async def finalize_run_with_lease(
        self,
        _run: PipelineRun,
        *,
        kind: RunKind,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Accept the fake terminal transition.

        Args:
            _run: Terminal row ignored by this behavior-neutral fake.
            kind: Lease kind under test.
            owner_id: Worker identity under test.
            generation: Fencing generation under test.
            now: Finalization time under test.

        Returns:
            Always true for service tests unrelated to fencing conflicts.
        """
        del kind, owner_id, generation, now
        return True


__all__ = ["SuccessfulRunLeaseMixin"]
