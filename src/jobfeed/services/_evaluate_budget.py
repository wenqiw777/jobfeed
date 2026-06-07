"""Budget reservation + daily-limit checks for real evaluation runs."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.evaluate_types import EvaluateLLMConfig


async def check_budget(
    store_ops: StoreOpsMixin,
    max_calls: int,
    max_cost: float,
    logger: JobfeedLogger,
) -> bool:
    """Returns True if budget allows more calls. False to stop.

    Best-effort under concurrency -- up to max_concurrent extra
    calls may slip through before the gate trips (all N workers
    can read the same call count simultaneously).

    Args:
        store_ops: Store operations port for cost queries.
        max_calls: Daily call limit.
        max_cost: Daily cost limit in USD.
        logger: Logger for budget warnings.

    Returns:
        True if budget is available.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    cost = await store_ops.get_cost(today)
    calls = cost.calls if cost else 0
    spent_usd = cost.spent_usd if cost else 0.0
    if calls >= max_calls:
        logger.warning("daily_call_limit_reached", calls=calls)
        return False
    if spent_usd >= max_cost:
        logger.warning("daily_cost_limit_reached", spent=spent_usd)
        return False
    return True


async def record_call_attempt(store_ops: StoreOpsMixin) -> str:
    """Reserve one daily LLM call attempt before invoking an external model.

    Args:
        store_ops: Store operations port.

    Returns:
        UTC ledger day used for the reservation and later spend recording.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    await store_ops.record_cost(day=today, spent_usd=0.0, calls=1)
    return today


class EvaluateBudgetGate:
    """Check and reserve daily LLM call budget for EvaluateService."""

    def __init__(
        self,
        store_ops: StoreOpsMixin,
        llm_config: EvaluateLLMConfig,
        logger: JobfeedLogger,
    ) -> None:
        self._store_ops = store_ops
        self._max_calls = llm_config.max_daily_score_calls
        self._max_cost_usd = llm_config.max_daily_cost_usd
        self._logger = logger

    async def has_budget(self) -> bool:
        """Return whether a pending row can be claimed for a paid call.

        Returns:
            True when budget allows another LLM call.
        """
        return await check_budget(
            self._store_ops,
            self._max_calls,
            self._max_cost_usd,
            self._logger,
        )

    async def reserve(self) -> str | None:
        """Reserve one call attempt and return its ledger day, if allowed.

        Returns:
            UTC ledger day, or None when budget is exhausted.
        """
        if not await self.has_budget():
            return None
        return await record_call_attempt(self._store_ops)
