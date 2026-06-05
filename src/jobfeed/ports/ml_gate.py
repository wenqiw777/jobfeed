"""Predict-only ML-gate port: filters jobs before paid LLM scoring.

The gate consumes lightweight ``GateInput`` records (job_id + title + JD text)
and returns one ``MLGateResult`` per input, in input order. Per the Phase 5
simplification (Rev3) the model is a required in-repo asset, so there is no
model-missing path and the port deliberately defines no ``ModelNotFoundError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import MLGateResult


@dataclass(frozen=True)
class GateInput:
    """Minimal posting payload the ML gate needs to score one job."""

    job_id: str
    title: str
    jd_text: str


@runtime_checkable
class MLGate(Protocol):
    """Adapter-neutral, predict-only ML-gate capability."""

    async def predict_batch(self, jobs: list[GateInput]) -> list[MLGateResult]:
        """Score a batch of jobs, one ordered result per input.

        Args:
            jobs: Gate inputs to score; ``result[i]`` corresponds to ``jobs[i]``.

        Returns:
            One ``MLGateResult`` per input, in the same order as ``jobs``.
        """
        ...
