"""Optional store protocol for atomic paid-evaluation claims."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jobfeed.domain.models import JobPosting


@runtime_checkable
class StoreEvaluationClaimMixin(Protocol):
    """Atomic pending evaluation claim queries for paid LLM runs."""

    async def claim_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Claim Stage A jobs for one real evaluation run.

        Args:
            limit: Max jobs.
            quality_bands: Optional JD quality allow-list.
            corpus: Corpus filter value.
            max_days: Freshness filter.

        Returns:
            Claimed Stage A jobs.
        """
        ...

    async def preview_claimable_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Preview Stage A jobs a real run would claim.

        Args:
            limit: Max jobs.
            quality_bands: Optional JD quality allow-list.
            corpus: Corpus filter value.
            max_days: Freshness filter.

        Returns:
            Claimable Stage A jobs without mutating lease state.
        """
        ...

    async def claim_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
    ) -> list[JobPosting]:
        """Claim Stage B jobs for one real evaluation run.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.
            stage_a_threshold: Optional minimum Stage A score.

        Returns:
            Claimed Stage B jobs.
        """
        ...

    async def release_stage_a_claim(self, job_id: str) -> None:
        """Release one unspent Stage A claim.

        Args:
            job_id: Store-assigned identity.
        """
        ...

    async def release_stage_b_claim(self, job_id: str) -> None:
        """Release one unspent Stage B claim.

        Args:
            job_id: Store-assigned identity.
        """
        ...

    async def refresh_stage_b_claim(self, job_id: str) -> None:
        """Refresh one active Stage B claim lease.

        Args:
            job_id: Store-assigned identity.
        """
        ...
