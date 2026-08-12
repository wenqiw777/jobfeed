"""Transition-only contracts for the PostgreSQL run-lifecycle bridge."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from jobfeed.adapters.store.legacy_run_leases import LegacyRunLeaseStore
from jobfeed.domain.models import PipelineRun


class _LegacyStore:
    def __init__(self) -> None:
        self.recorded: list[PipelineRun] = []
        self.updated: list[PipelineRun] = []

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        self.recorded.append(run)

    async def update_pipeline_run_status(self, run: PipelineRun) -> None:
        self.updated.append(run)


async def test_legacy_bridge_preserves_pg_until_sqlite_cutover() -> None:
    """The explicit bridge maps the new orchestration port to legacy PG writes."""
    legacy = _LegacyStore()
    bridge = LegacyRunLeaseStore(legacy)
    now = datetime(2026, 8, 12, 21, 0, tzinfo=UTC)
    run = PipelineRun(run_id=str(uuid4()), started_at=now, source="scan")
    owner_id = str(uuid4())

    assert (
        await bridge.start_run_with_lease(run, kind="scan", owner_id=owner_id, now=now)
        == 1
    )
    assert await bridge.renew_run_lease(
        kind="scan",
        owner_id=owner_id,
        run_id=run.run_id,
        generation=1,
        now=now,
    )
    run.status = "succeeded"
    run.finished_at = now
    assert await bridge.finalize_run_with_lease(
        run,
        kind="scan",
        owner_id=owner_id,
        generation=1,
        now=now,
    )

    assert legacy.recorded == [run]
    assert legacy.updated == [run]
