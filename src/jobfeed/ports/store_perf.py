"""Performance observation store port.

Records step-level timing data for pipeline runs so the observability
layer can surface per-step latency, error rates, and trend data.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models_perf import StepTiming


@runtime_checkable
class StorePerfMixin(Protocol):
    """Step-timing persistence capability."""

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Persist a single step timing record.

        Args:
            timing: Step timing to persist.
        """
        ...

    async def record_step_timings(self, timings: list[StepTiming]) -> None:
        """Persist multiple step timing records in a single batch.

        Args:
            timings: Step timings to persist.
        """
        ...
