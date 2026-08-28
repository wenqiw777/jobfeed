"""Optional store protocol for atomic paid-evaluation claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import JobPosting


@dataclass(frozen=True)
class GateCandidate:
    """A non-claiming gate candidate plus its persisted ML-gate state.

    Surfacing ``ml_gate_result`` lets the funnel skip re-gating a rep that is
    already persisted ``'pass'`` (avoiding a model/threshold swap silently
    flipping it to ``fail`` and dropping it from survivors). ``None`` means the
    row has not been gated yet; ``'fail'`` rows are pre-excluded by the load
    predicate when ``exclude_gate_failed`` is set.
    """

    job: JobPosting
    ml_gate_result: str | None


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

    async def load_gate_candidates(
        self,
        *,
        corpus: str = "unrated",
        quality_bands: frozenset[str] | None = None,
        max_days: int | None = None,
        limit: int = 100,
        exclude_gate_failed: bool = True,
        after: tuple[datetime, int] | None = None,
        job_ids: list[str] | None = None,
    ) -> list[GateCandidate]:
        """Load ML-gate candidates pending Stage A without claiming.

        Args:
            corpus: Corpus filter value.
            quality_bands: Optional JD quality allow-list.
            max_days: Freshness filter on discovered_at.
            limit: Max jobs (one page).
            exclude_gate_failed: When True, drop rows whose gate result is 'fail';
                when False, gate state is ignored entirely.
            after: Optional ``(discovered_at, id)`` keyset cursor; when set, only
                rows strictly past it in ``discovered_at DESC, id DESC`` order are
                returned (used to page past hard-filtered drops).

        Returns:
            Gate candidates (job + persisted ``ml_gate_result``) pending Stage A,
            without mutating lease state.
        """
        ...

    async def claim_stage_a_by_ids(
        self,
        job_ids: list[str],
        *,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
        limit: int = 100,
    ) -> list[JobPosting]:
        """Atomically claim Stage A jobs restricted to an explicit id set.

        Args:
            job_ids: Store-assigned identities to claim from.
            quality_bands: Optional JD quality allow-list.
            corpus: Corpus filter value.
            max_days: Freshness filter on discovered_at.
            limit: Max jobs.

        Returns:
            Claimed Stage A jobs (a subset of ``job_ids``); ``[]`` for empty input.
        """
        ...

    async def claim_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
        job_ids: list[str] | None = None,
    ) -> list[JobPosting]:
        """Claim Stage B jobs for one real evaluation run.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.
            stage_a_threshold: Optional minimum Stage A score.
            job_ids: Optional store identities restricting the claim.

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
