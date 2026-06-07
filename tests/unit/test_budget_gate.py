"""Unit tests for the EvaluateService budget gate logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.models import CostEntry, LLMResponse, LLMUsage
from jobfeed.services._evaluate_budget import check_budget, record_call_attempt
from jobfeed.services._evaluate_helpers import UsageRecordContext, record_usage


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
        self.costs: list[tuple[str, float, int]] = []
        self.usages: list[LLMUsage] = []

    async def get_cost(self, _day: str) -> CostEntry | None:
        """Return the configured cost entry."""
        return self._cost_entry

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Record cost mutations for assertions."""
        self.costs.append((day, spent_usd, calls))

    async def record_llm_usage(self, usage: LLMUsage) -> None:
        """Record LLM usage rows for assertions."""
        self.usages.append(usage)

    async def record_llm_usage_with_cost(
        self,
        *,
        day: str,
        spent_usd: float,
        usage: LLMUsage,
    ) -> None:
        """Record cost and usage rows for assertions."""
        self.costs.append((day, spent_usd, 0))
        self.usages.append(usage)


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


@pytest.mark.asyncio
async def test_budget_zero_call_limit_blocks_without_cost_entry() -> None:
    """A zero call budget should block before the first daily ledger row."""
    ops = StubStoreOps(cost_entry=None)
    logger = RecordingLogger()

    result = await check_budget(ops, max_calls=0, max_cost=10.0, logger=logger)  # type: ignore[arg-type]

    assert result is False
    assert any(ev[0] == "daily_call_limit_reached" for ev in logger.events)


@pytest.mark.asyncio
async def test_budget_zero_cost_limit_blocks_without_cost_entry() -> None:
    """A zero cost budget should block before the first daily ledger row."""
    ops = StubStoreOps(cost_entry=None)
    logger = RecordingLogger()

    result = await check_budget(ops, max_calls=150, max_cost=0.0, logger=logger)  # type: ignore[arg-type]

    assert result is False
    assert any(ev[0] == "daily_cost_limit_reached" for ev in logger.events)


@pytest.mark.asyncio
async def test_record_call_attempt_returns_reserved_day() -> None:
    """Call reservation should return the ledger day it wrote."""
    ops = StubStoreOps()

    day = await record_call_attempt(ops)  # type: ignore[arg-type]

    assert ops.costs == [(day, 0.0, 1)]
    datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_record_usage_uses_reserved_ledger_day() -> None:
    """Cost spend should use the reservation day, not a recomputed day."""
    ops = StubStoreOps()
    response = LLMResponse(
        content="{}",
        model="mock-model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.25,
        cached=True,
        latency_ms=123,
    )

    await record_usage(
        ops,  # type: ignore[arg-type]
        response,
        UsageRecordContext("job-1", "b", "run-1", "2026-05-24"),
    )

    assert ops.costs == [("2026-05-24", 0.25, 0)]
    assert len(ops.usages) == 1
    usage = ops.usages[0]
    assert usage.model == "mock-model"
    assert usage.job_id == "job-1"
    assert usage.stage == "b"
    assert usage.run_id == "run-1"


async def test_record_usage_with_none_cost_degrades_to_zero() -> None:
    """An unpriced backend (e.g. openai-compat on deepseek/minimax) returns
    ``cost_usd=None``; the dollar tally must degrade to $0 without crashing while
    the call counter (recorded separately by ``record_call_attempt``) is
    unaffected — i.e. the budget gate degrades to call-count-only.
    """
    ops = StubStoreOps()
    response = LLMResponse(
        content="{}",
        model="deepseek-chat",
        input_tokens=10,
        output_tokens=5,
        cost_usd=None,
        cached=False,
        latency_ms=42,
    )

    await record_usage(
        ops,  # type: ignore[arg-type]
        response,
        UsageRecordContext("job-2", "a", "run-2", "2026-05-24"),
    )

    assert ops.costs == [("2026-05-24", 0.0, 0)]
    assert ops.usages[0].cost_usd == 0.0
