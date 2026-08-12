"""Service routing and PostgreSQL transition bridge threshold contracts."""

from __future__ import annotations

from jobfeed.adapters.store.legacy_stage_b_threshold import (
    LegacyPostgresStageBThresholdSync,
)
from jobfeed.services._evaluate_claims import sync_stage_b_threshold


class _AtomicStore:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    async def sync_stage_b_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> tuple[int, int]:
        self.calls.append((threshold, max_days))
        return (2, 3)


class _LegacyStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int | None]] = []

    async def reopen_stage_b_at_or_above_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        self.calls.append(("reopen", threshold, max_days))
        return 4

    async def mark_stage_b_below_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        self.calls.append(("skip", threshold, max_days))
        return 5


async def test_service_prefers_store_atomic_threshold_capability() -> None:
    """An atomic store is used even when an explicit legacy bridge is present."""
    store = _AtomicStore()
    legacy = _LegacyStore()
    bridge = LegacyPostgresStageBThresholdSync(legacy)

    counts = await sync_stage_b_threshold(  # type: ignore[arg-type]
        store,
        70,
        14,
        transition_sync=bridge,
    )

    assert counts == (2, 3)
    assert store.calls == [(70, 14)]
    assert legacy.calls == []


async def test_explicit_postgres_transition_bridge_preserves_order_and_counts() -> None:
    """The bounded PostgreSQL bridge reopens before skipping and returns counts."""
    store = _LegacyStore()
    bridge = LegacyPostgresStageBThresholdSync(store)

    counts = await sync_stage_b_threshold(  # type: ignore[arg-type]
        store,
        80,
        7,
        transition_sync=bridge,
    )

    assert counts == (4, 5)
    assert store.calls == [("reopen", 80, 7), ("skip", 80, 7)]
