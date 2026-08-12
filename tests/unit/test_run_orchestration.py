"""Unit contract for shared scan/evaluate run-lease orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.models import PipelineRun
from jobfeed.services.run_orchestration import RunLeaseOrchestrator

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)
OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")


class LeaseStore:
    """In-memory lease port double with an ordered call trace."""

    def __init__(self, *, generation: int | None = 7) -> None:
        self.generation = generation
        self.renew_result = True
        self.calls: list[tuple[str, object]] = []

    async def start_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: str,
        owner_id: str,
        now: datetime,
    ) -> int | None:
        """Record an atomic start request."""
        self.calls.append(("start", (run, kind, owner_id, now)))
        return self.generation

    async def renew_run_lease(
        self,
        *,
        kind: str,
        owner_id: str,
        run_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Record a fenced renewal request."""
        self.calls.append(
            ("renew", (kind, owner_id, run_id, generation, now))
        )
        return self.renew_result

    async def finalize_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: str,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Record a fenced terminal transition."""
        self.calls.append(
            ("finalize", (run, kind, owner_id, generation, now))
        )
        return True


def _orchestrator(
    store: LeaseStore, *, heartbeat_interval_seconds: float = 60
) -> RunLeaseOrchestrator:
    return RunLeaseOrchestrator(
        store,
        clock=lambda: NOW,
        owner_id=OWNER_ID,
        run_id_factory=lambda: RUN_ID,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


@pytest.mark.asyncio
async def test_atomic_start_and_heartbeat_precede_business_scheduling() -> None:
    """No external work starts before the atomic row+lease start and heartbeat."""
    store = LeaseStore()
    orchestrator = _orchestrator(store)
    observed: list[str] = []

    async def _work(session: object) -> None:
        observed.append("work")
        assert store.calls[0][0] == "start"
        assert session.heartbeat_running  # type: ignore[attr-defined]

    run = await orchestrator.run("scan", "indeed", _work)

    assert observed == ["work"]
    assert run.status == "succeeded"
    assert [name for name, _ in store.calls] == ["start", "finalize"]
    _, (_, kind, owner_id, generation, now) = store.calls[-1]
    assert (kind, owner_id, generation, now) == (
        "scan",
        str(OWNER_ID),
        7,
        NOW,
    )


@pytest.mark.asyncio
async def test_renewal_loss_blocks_new_work_and_never_uses_legacy_finalize() -> None:
    """A stale owner cannot schedule more work or fall back to legacy updates."""
    store = LeaseStore()
    store.renew_result = False
    orchestrator = _orchestrator(store, heartbeat_interval_seconds=0.001)

    async def _work(session: object) -> None:
        while not session.lease_lost:  # type: ignore[attr-defined]
            await asyncio.sleep(0)
        session.ensure_active()  # type: ignore[attr-defined]

    with pytest.raises(RunLeaseLostError):
        await orchestrator.run("evaluate", "evaluate", _work)

    assert [name for name, _ in store.calls] == ["start", "renew", "finalize"]
    assert not hasattr(store, "update_pipeline_run_status")


@pytest.mark.asyncio
async def test_conflicting_atomic_start_never_runs_business_work() -> None:
    """A lease conflict leaves no pipeline work scheduled."""
    store = LeaseStore(generation=None)
    orchestrator = _orchestrator(store)
    ran = False

    async def _work(_session: object) -> None:
        nonlocal ran
        ran = True

    with pytest.raises(Exception, match="already active"):
        await orchestrator.run("scan", "scan", _work)

    assert not ran
    assert [name for name, _ in store.calls] == ["start"]


@pytest.mark.asyncio
async def test_naive_injected_clock_fails_before_store_mutation() -> None:
    """Lease timestamps must be aware UTC values at the service boundary."""
    store = LeaseStore()
    orchestrator = RunLeaseOrchestrator(
        store,
        clock=lambda: datetime(2026, 8, 12, 14, 30),
        owner_id=OWNER_ID,
        run_id_factory=lambda: RUN_ID,
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        await orchestrator.start("scan", "scan")

    assert store.calls == []
