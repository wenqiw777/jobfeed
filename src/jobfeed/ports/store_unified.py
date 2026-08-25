"""Persistence port for the independent unified evaluator lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from jobfeed.domain.models import JobPosting

if TYPE_CHECKING:
    from jobfeed.domain.models import UnifiedEvaluationResult  # type: ignore[attr-defined]  # noqa: I001


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
        """Claim pending jobs without redoing current-version completions."""
        ...

    async def preview_pending_evaluations(
        self,
        *,
        evaluator_version: str,
        corpus: str = "unrated",
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Read pending jobs without changing their claim state."""
        ...

    async def save_evaluation(
        self,
        job_id: str,
        result: UnifiedEvaluationResult,
    ) -> None:
        """Atomically upsert one completed current evaluation."""
        ...

    async def save_evaluation_error(
        self,
        job_id: str,
        error: str,
        evaluator_version: str,
    ) -> None:
        """Persist one failed current evaluation attempt."""
        ...

    async def release_evaluation_claim(
        self,
        job_id: str,
        evaluator_version: str,
    ) -> None:
        """Release an active claim owned by the supplied evaluator version."""
        ...

    async def get_current_evaluation(self, job_id: str) -> dict[str, object] | None:
        """Read one current unified result row when present."""
        ...


__all__ = ["StoreUnifiedEvaluationMixin"]
