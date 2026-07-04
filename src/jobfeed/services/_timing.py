"""Async context manager that records step timing and wraps in an OTel span."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from jobfeed.domain.models_perf import (
    FunnelStats,
    LLMDailyStats,
    PerformanceOverview,
    StepTiming,
    StepTimingSeries,
)
from jobfeed.observability import SpanWrapper, get_logger
from jobfeed.ports.store_perf import StorePerfMixin

if TYPE_CHECKING:
    from types import TracebackType


class StepTimer:
    """Async context manager that records step timing and wraps in an OTel span.

    On exit the elapsed wall-clock time is persisted as a :class:`StepTiming`
    via the store's ``record_step_timing`` method.  If the wrapped block raises,
    ``is_error`` is set and the exception re-raised.
    """

    def __init__(
        self,
        store: StorePerfMixin,
        run_id: str,
        step_type: str,
        step_name: str,
        tracer: SpanWrapper,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._step_type = step_type
        self._step_name = step_name
        self._tracer = tracer
        self._start: float = 0.0
        self._span_cm: Any = None

    async def __aenter__(self) -> StepTimer:
        """Enter the span and start the monotonic clock."""
        self._span_cm = self._tracer.start_as_current_span(self._step_name)
        self._span_cm.__enter__()
        self._start = time.monotonic()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        """Record elapsed time and close the span.

        The store write is best-effort: timing is observability data, so a
        failed INSERT is logged and swallowed rather than allowed to fail
        the business work it wraps (the pipeline isolates even source and
        scoring errors — a metrics write must not be the thing that marks
        a successful run as failed). The span always closes, and a block
        exception propagates unchanged (returns False).
        """
        elapsed_ms = (time.monotonic() - self._start) * 1000
        is_error = exc_type is not None
        timing = StepTiming(
            run_id=self._run_id,
            step_type=self._step_type,
            step_name=self._step_name,
            elapsed_ms=elapsed_ms,
            is_error=is_error,
        )
        try:
            await self._store.record_step_timing(timing)
        except Exception as exc:
            get_logger().warning(
                "step_timing_write_failed",
                run_id=self._run_id,
                step_type=self._step_type,
                step_name=self._step_name,
                error=str(exc),
            )
        finally:
            if self._span_cm is not None:
                self._span_cm.__exit__(exc_type, exc_val, exc_tb)
        return False  # never swallow block exceptions


class _NullPerfStore:
    """No-op StorePerfMixin for stores that lack perf recording."""

    async def record_step_timing(self, _timing: object) -> None:
        """Silently discard the timing record."""

    async def record_step_timings(self, _timings: object) -> None:
        """Silently discard the timing records."""

    async def get_performance_overview(self, _window_days: int) -> PerformanceOverview:
        """Return an empty overview."""
        return PerformanceOverview(
            avg_scan_duration_ms=0.0,
            avg_eval_duration_ms=0.0,
            total_llm_cost_usd=0.0,
            error_rate=0.0,
            scan_duration_delta=None,
            eval_duration_delta=None,
            cost_delta=None,
            error_rate_delta=None,
        )

    async def get_step_timings(
        self, _window_days: int, _step_type: str | None = None
    ) -> list[StepTimingSeries]:
        """Return an empty list."""
        return []

    async def get_llm_daily_stats(self, _window_days: int) -> list[LLMDailyStats]:
        """Return an empty list."""
        return []

    async def get_funnel_stats(self, _window_days: int) -> list[FunnelStats]:
        """Return an empty list."""
        return []


def get_perf_store(store: object) -> StorePerfMixin:
    """Return the store as StorePerfMixin, falling back to a no-op.

    Args:
        store: Any store object, possibly implementing StorePerfMixin.

    Returns:
        The store cast to StorePerfMixin, or a silent no-op if unsupported.
    """
    if isinstance(store, StorePerfMixin):
        return store
    return _NullPerfStore()


__all__ = ["StepTimer", "get_perf_store"]
