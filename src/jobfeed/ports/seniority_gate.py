"""Ports for the independent seniority eligibility gate."""

from __future__ import annotations

from typing import Protocol

from jobfeed.domain.seniority import SeniorityDecision, SeniorityInput


class SeniorityModel(Protocol):
    """Probability model used only when deterministic rules are unclear."""

    async def predict_out_of_scope(self, jobs: list[SeniorityInput]) -> list[float]:
        """Return one out-of-scope probability per input, in input order.

        Args:
            jobs: Ambiguous seniority inputs.

        Returns:
            Ordered probabilities in the inclusive range ``[0, 1]``.
        """
        ...


class SeniorityGate(Protocol):
    """Rule-plus-model seniority eligibility boundary."""

    async def predict_batch(
        self, jobs: list[SeniorityInput]
    ) -> list[SeniorityDecision]:
        """Return one ordered seniority decision per input.

        Args:
            jobs: Candidate postings to classify.

        Returns:
            One explainable seniority decision per input.
        """
        ...


__all__ = ["SeniorityGate", "SeniorityModel"]
