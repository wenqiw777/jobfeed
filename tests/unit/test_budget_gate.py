"""Unit tests for the EvaluateService budget gate logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.models import CostEntry, LLMUsage
from jobfeed.services._evaluate_helpers import check_budget


class RecordingLogger:
    """Minimal logger that records warning events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> object:
        """Record a warning event."""
        item = (event, kwargs)
        self.events.append(item)
        return item

    def info(self, event: str, **kwargs: object) -> object:
        """Record an info event."""
        return (event, kwargs)

    def error(self, event: str, **kwargs: object) -> object:
        """Record an error event."""
        return (event, kwargs)

    def debug(self, event: str, **kwargs: object) -> object:
        """Record a debug event."""
        return (event, kwargs)


class StubStoreOps:
    """Minimal StoreOpsMixin for budget gate tests."""

    def __init__(self, cost_entry: CostEntry | None = None) -> None:
        self._cost_entry = cost_entry

    async def get_cost(self, _day: str) -> CostEntry | None:
        """Return the configured cost entry."""
        return self._cost_entry

    async def record_cost(self, *, day: str, spent_usd: float) -> None:
        """No-op for budget tests."""

    async def record_llm_usage(self, _usage: LLMUsage) -> None:
        """No-op for budget tests."""


@pytest.mark.asyncio
async def test_budget_not_exceeded_returns_true() -> None:
    """Budget check passes when both call count and cost are below limits."""
    entry = CostEntry(
        day="2026-05-25",
        spent_usd=1.0,
        calls=10,
        last_updated=datetime.now(UTC),
    )
    ops = StubStoreOps(cost_entry=entry)
    logger = RecordingLogger()

    result = await check_budget(ops, max_calls=150, max_cost=10.0, logger=logger)  # type: ignore[arg-type]

    assert result is True
    assert len(logger.events) == 0


@pytest.mark.asyncio
async def test_budget_call_count_exceeded_returns_false() -> None:
    """Budget check fails when call count meets or exceeds the limit."""
    entry = CostEntry(
        day="2026-05-25",
        spent_usd=1.0,
        calls=150,
        last_updated=datetime.now(UTC),
    )
    ops = StubStoreOps(cost_entry=entry)
    logger = RecordingLogger()

    result = await check_budget(ops, max_calls=150, max_cost=10.0, logger=logger)  # type: ignore[arg-type]

    assert result is False
    assert any(ev[0] == "daily_call_limit_reached" for ev in logger.events)


@pytest.mark.asyncio
async def test_budget_cost_exceeded_returns_false() -> None:
    """Budget check fails when cost meets or exceeds the limit."""
    entry = CostEntry(
        day="2026-05-25",
        spent_usd=10.0,
        calls=5,
        last_updated=datetime.now(UTC),
    )
    ops = StubStoreOps(cost_entry=entry)
    logger = RecordingLogger()

    result = await check_budget(ops, max_calls=150, max_cost=10.0, logger=logger)  # type: ignore[arg-type]

    assert result is False
    assert any(ev[0] == "daily_cost_limit_reached" for ev in logger.events)


@pytest.mark.asyncio
async def test_budget_first_call_of_day_returns_true() -> None:
    """Budget check passes when no cost entry exists (first call of day)."""
    ops = StubStoreOps(cost_entry=None)
    logger = RecordingLogger()

    result = await check_budget(ops, max_calls=150, max_cost=10.0, logger=logger)  # type: ignore[arg-type]

    assert result is True
    assert len(logger.events) == 0
