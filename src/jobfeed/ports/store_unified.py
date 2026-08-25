"""Persistence port for the independent unified evaluator lifecycle."""

from __future__ import annotations

from typing import Protocol

from jobfeed.domain.models import JobPosting, UnifiedEvaluationResult


class StoreUnifiedEvaluationMixin(Protocol):
    """Version-aware claim and persistence boundary for unified evaluation."""

    async def claim_pending_evaluations(
        self,
        *,
        evaluator_version: str,
        corpus: str = "unrated",
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Claim pending jobs without redoing current-version completions.

        Args:
            evaluator_version: Exact evaluator contract identity.
            corpus: Pending-work selection mode.
            limit: Maximum jobs to claim.
            max_days: Optional discovery freshness window.

        Returns:
            Atomically claimed jobs.
        """
        ...

    async def preview_pending_evaluations(
        self,
        *,
        evaluator_version: str,
        corpus: str = "unrated",
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Read pending jobs without changing their claim state.

        Args:
            evaluator_version: Exact evaluator contract identity.
            corpus: Pending-work selection mode.
            limit: Maximum jobs to preview.
            max_days: Optional discovery freshness window.

        Returns:
            Matching unclaimed jobs.
        """
        ...

    async def save_evaluation(
        self,
        job_id: str,
        result: UnifiedEvaluationResult,
    ) -> None:
        """Atomically upsert one completed current evaluation.

        Args:
            job_id: Store-assigned job identity.
            result: Validated canonical evaluation.
        """
        ...

    async def save_evaluation_error(
        self,
        job_id: str,
        error: str,
        evaluator_version: str,
    ) -> None:
        """Persist one failed current evaluation attempt.

        Args:
            job_id: Store-assigned job identity.
            error: Latest error detail.
            evaluator_version: Evaluator version that failed.
        """
        ...

    async def release_evaluation_claim(
        self,
        job_id: str,
        evaluator_version: str,
    ) -> None:
        """Release an active claim owned by the supplied evaluator version.

        Args:
            job_id: Store-assigned job identity.
            evaluator_version: Version owning the claim.
        """
        ...

    async def get_current_evaluation(self, job_id: str) -> dict[str, object] | None:
        """Read one current unified result row when present.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Decoded canonical row, or None.
        """
        ...


__all__ = ["StoreUnifiedEvaluationMixin"]
