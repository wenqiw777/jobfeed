"""Hybrid seniority gate: deterministic boundary first, model only on ambiguity."""

from __future__ import annotations

from dataclasses import replace

from jobfeed.domain.seniority import (
    SeniorityDecision,
    SeniorityInput,
    SeniorityResult,
    classify_seniority_rule,
)
from jobfeed.ports.seniority_gate import SeniorityModel


class HybridSeniorityGate:
    """Preserve explicit user rules while delegating unclear roles to a model."""

    def __init__(
        self,
        *,
        model: SeniorityModel | None,
        out_of_scope_threshold: float,
        version: str | None = None,
    ) -> None:
        if not 0.0 <= out_of_scope_threshold <= 1.0:
            raise ValueError("seniority threshold must be in [0, 1]")
        self._model = model
        self._threshold = out_of_scope_threshold
        self._version = version

    async def predict_batch(
        self, jobs: list[SeniorityInput]
    ) -> list[SeniorityDecision]:
        """Apply explicit rules first and model only the unclear subset.

        Args:
            jobs: Candidate postings to classify.

        Returns:
            Ordered rule or model decisions.

        Raises:
            ValueError: If model scores are invalid or misaligned.
        """
        decisions = [classify_seniority_rule(job.title, job.jd_text) for job in jobs]
        unclear_indexes = [
            index
            for index, decision in enumerate(decisions)
            if decision.result == "unclear"
        ]
        if self._model is None or not unclear_indexes:
            return decisions
        unclear_jobs = [jobs[index] for index in unclear_indexes]
        scores = await self._model.predict_out_of_scope(unclear_jobs)
        if len(scores) != len(unclear_jobs):
            raise ValueError("seniority model returned the wrong number of scores")
        for index, score in zip(unclear_indexes, scores, strict=True):
            if not 0.0 <= score <= 1.0:
                raise ValueError("seniority model score must be in [0, 1]")
            result: SeniorityResult = (
                "out_of_scope" if score >= self._threshold else "in_scope"
            )
            decisions[index] = replace(
                decisions[index],
                result=result,
                reason="model seniority classification",
                confidence=score if result == "out_of_scope" else 1.0 - score,
                source="model",
                version=self._version or "model-unknown",
            )
        return decisions


__all__ = ["HybridSeniorityGate"]
