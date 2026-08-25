"""Public SQLite capability for the independent unified evaluator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from jobfeed.adapters.store import _sqlite_evaluation_results
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import JobPosting

if TYPE_CHECKING:
    from jobfeed.domain.models import UnifiedEvaluationResult  # type: ignore[attr-defined]  # noqa: I001


class SqliteUnifiedEvaluations:
    """Persist, claim, release, and read current unified evaluations."""

    _lifecycle: SqliteLifecycle

    async def claim_pending_evaluations(
        self,
        *,
        evaluator_version: str,
        corpus: str = "unrated",
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Atomically claim work not completed by the requested version.

        Args:
            evaluator_version: Exact evaluator contract/version identity.
            corpus: ``all`` pending work or only ``failed`` rows.
            limit: Maximum jobs to claim.
            max_days: Optional discovery freshness window.

        Returns:
            Claimed jobs in stable discovery order.
        """
        return await _sqlite_evaluation_results._claim_pending_evaluations(
            self._lifecycle,
            evaluator_version=evaluator_version,
            corpus=corpus,
            limit=limit,
            max_days=max_days,
            now=self._unified_evaluation_now(),
        )

    async def preview_pending_evaluations(
        self,
        *,
        evaluator_version: str,
        corpus: str = "unrated",
        limit: int = 100,
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Read pending work using the claim filter without mutating it."""
        return await _sqlite_evaluation_results._preview_pending_evaluations(
            self._lifecycle,
            evaluator_version=evaluator_version,
            corpus=corpus,
            limit=limit,
            max_days=max_days,
            now=self._unified_evaluation_now(),
        )

    async def save_evaluation(
        self,
        job_id: str,
        result: UnifiedEvaluationResult,
    ) -> None:
        """Atomically upsert one completed unified evaluation.

        Args:
            job_id: Decimal SQLite job identity.
            result: Validated unified evaluator result.
        """
        await _sqlite_evaluation_results._save_evaluation(
            self._lifecycle,
            job_id,
            result,
            now=self._unified_evaluation_now(),
        )

    async def save_evaluation_error(
        self,
        job_id: str,
        error: str,
        evaluator_version: str,
    ) -> None:
        """Persist one failed unified-evaluation attempt atomically.

        Args:
            job_id: Decimal SQLite job identity.
            error: Latest failure detail.
            evaluator_version: Evaluator version that failed.
        """
        await _sqlite_evaluation_results._save_evaluation_error(
            self._lifecycle,
            job_id,
            error,
            evaluator_version,
            now=self._unified_evaluation_now(),
        )

    async def release_evaluation_claim(
        self,
        job_id: str,
        evaluator_version: str,
    ) -> None:
        """Idempotently release a claim owned by one evaluator version.

        Args:
            job_id: Decimal SQLite job identity.
            evaluator_version: Exact version used to claim the row.
        """
        await _sqlite_evaluation_results._release_evaluation_claim(
            self._lifecycle,
            job_id,
            evaluator_version,
            now=self._unified_evaluation_now(),
        )

    async def get_current_evaluation(self, job_id: str) -> dict[str, object] | None:
        """Read the current result independently of legacy evaluations.

        Args:
            job_id: Decimal SQLite job identity.

        Returns:
            Decoded persisted row, or ``None`` when absent.
        """
        return await _sqlite_evaluation_results._get_current_evaluation(
            self._lifecycle, job_id
        )

    def _unified_evaluation_now(self) -> datetime:
        clock = getattr(self, "_now", None)
        if callable(clock):
            return cast(datetime, clock())
        return datetime.now(UTC)


__all__ = ["SqliteUnifiedEvaluations"]
