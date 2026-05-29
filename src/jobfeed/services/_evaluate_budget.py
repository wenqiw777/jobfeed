"""Budget reservation helper for real evaluation runs."""

from __future__ import annotations

from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services._evaluate_helpers import check_budget, record_call_attempt
from jobfeed.services.evaluate_types import EvaluateLLMConfig


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
