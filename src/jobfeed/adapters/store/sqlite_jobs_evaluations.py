"""Public SQLite jobs and evaluations capability composed over a lifecycle."""

from __future__ import annotations

from jobfeed.adapters.store import (
    _sqlite_evaluation_batch,
    _sqlite_evaluation_queries,
    _sqlite_evaluations,
    _sqlite_jobs,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import (
    JobEvaluation,
    JobPosting,
    MLGateResult,
    SaveJobResult,
    StageAResult,
    StageBResult,
)


class SqliteJobsEvaluations:
    """Persist jobs and evaluations through short lifecycle connections."""

    def __init__(self, lifecycle: SqliteLifecycle) -> None:
        """Bind the capability to an already-owned SQLite lifecycle."""
        self._lifecycle = lifecycle

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Insert or quality-aware update a job by natural key.

        Args: job posting to persist.
        Returns: truthful insert/update outcome and store identity.
        """
        return await _sqlite_jobs._save_job(self._lifecycle, job)

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load a job by decimal store identity.

        Args: decimal SQLite job identity.
        Returns: hydrated posting when present, otherwise None.
        """
        return await _sqlite_jobs._get_job(self._lifecycle, job_id)

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List recent jobs in stable descending order.

        Args: maximum nonnegative result count.
        Returns: jobs ordered by discovery time and identity descending.
        """
        return await _sqlite_jobs._list_jobs(self._lifecycle, limit)

    async def job_exists(self, *, platform: str, canonical_id: str) -> bool:
        """Check an exact natural key without mutation.

        Args: exact source platform and source-local identity.
        Returns: whether the natural key exists.
        """
        return await _sqlite_jobs._job_exists(
            self._lifecycle,
            platform=platform,
            canonical_id=canonical_id,
        )

    async def save_ml_gate_result(
        self,
        job_id: str,
        result: MLGateResult,
    ) -> None:
        """Persist the latest ML-gate decision and features.

        Args: decimal job identity and validated gate result.
        """
        await _sqlite_jobs._save_ml_gate_result(self._lifecycle, job_id, result)

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None:
        """Persist Stage A success and atomically advance new jobs to scored.

        Args: decimal job identity and validated Stage A result.
        """
        await _sqlite_evaluations._save_stage_a(self._lifecycle, job_id, result)

    async def save_stage_a_error(self, job_id: str, error: str) -> None:
        """Record one retryable Stage A error.

        Args: decimal job identity and latest error detail.
        """
        await _sqlite_evaluations._save_stage_a_error(self._lifecycle, job_id, error)

    async def save_stage_b(self, job_id: str, result: StageBResult) -> None:
        """Persist a successful Stage B result.

        Args: decimal job identity and validated structured Stage B result.
        """
        await _sqlite_evaluations._save_stage_b(self._lifecycle, job_id, result)

    async def save_stage_b_error(self, job_id: str, error: str) -> None:
        """Record one retryable Stage B error.

        Args: decimal job identity and latest error detail.
        """
        await _sqlite_evaluations._save_stage_b_error(self._lifecycle, job_id, error)

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        """Skip Stage B unless a completed result already exists.

        Args: decimal SQLite job identity.
        """
        await _sqlite_evaluations._mark_stage_b_skipped(self._lifecycle, job_id)

    async def load_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load the non-claiming Stage A corpus.

        Args: limit, quality filter, corpus, and optional freshness window.
        Returns: eligible jobs in stable descending order.
        """
        return await _sqlite_evaluation_queries._load_pending_stage_a(
            self._lifecycle,
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
        )

    async def load_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
    ) -> list[JobPosting]:
        """Load non-claiming Stage B null/error rows.

        Args: limit, optional freshness, and optional Stage A threshold.
        Returns: eligible jobs in stable descending order.
        """
        return await _sqlite_evaluation_queries._load_pending_stage_b(
            self._lifecycle,
            limit=limit,
            max_days=max_days,
            stage_a_threshold=stage_a_threshold,
        )

    async def list_evaluated_jobs(self, limit: int = 100) -> list[JobEvaluation]:
        """List joined evaluations in stable recency order.

        Args: maximum nonnegative result count.
        Returns: hydrated evaluation rows ordered by job recency.
        """
        return await _sqlite_evaluation_queries._list_evaluated_jobs(
            self._lifecycle, limit
        )

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Load one left-joined evaluation by job identity.

        Args: decimal SQLite job identity.
        Returns: evaluation wrapper for an existing job, otherwise None.
        """
        return await _sqlite_evaluation_queries._get_evaluation(self._lifecycle, job_id)

    async def top_evaluated_jobs(
        self,
        *,
        min_score: int = 0,
        limit: int = 100,
    ) -> list[JobEvaluation]:
        """List completed evaluations by score with stable tie-breaks.

        Args: inclusive score floor and maximum nonnegative result count.
        Returns: completed Stage B evaluations in stable ranked order.
        """
        return await _sqlite_evaluation_queries._top_evaluated_jobs(
            self._lifecycle,
            min_score=min_score,
            limit=limit,
        )

    async def get_stage_a_scores(self, job_ids: list[str]) -> dict[str, int | None]:
        """Batch-fetch Stage A scores after strict ID validation.

        Args: decimal job identities; malformed members abort the operation.
        Returns: mapping for identities having evaluation rows.
        """
        return await _sqlite_evaluation_batch._get_stage_a_scores(
            self._lifecycle, job_ids
        )

    async def mark_stage_b_skipped_batch(self, job_ids: list[str]) -> None:
        """Skip multiple Stage B rows after validating the whole batch.

        Args: decimal job identities; malformed members abort the operation.
        """
        await _sqlite_evaluation_batch._mark_stage_b_skipped_batch(
            self._lifecycle, job_ids
        )

    async def mark_stage_b_below_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        """Skip eligible Stage B rows below the active threshold.

        Args: exclusive score bound and optional freshness window.
        Returns: number of rows newly skipped.
        """
        return await _sqlite_evaluation_batch._mark_stage_b_below_threshold(
            self._lifecycle,
            threshold,
            max_days=max_days,
        )

    async def reopen_stage_b_at_or_above_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        """Reopen skipped Stage B rows meeting the active threshold.

        Args: inclusive score floor and optional freshness window.
        Returns: number of rows reopened.
        """
        return await _sqlite_evaluation_batch._reopen_stage_b_at_or_above_threshold(
            self._lifecycle,
            threshold,
            max_days=max_days,
        )

    async def preview_pending_stage_b_after_threshold_sync(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int,
    ) -> list[JobPosting]:
        """Preview post-sync Stage B eligibility without mutation.

        Args: limit, optional freshness, and active Stage A threshold.
        Returns: jobs a threshold sync followed by claim could consider.
        """
        preview = _sqlite_evaluation_batch._preview_pending_stage_b_after_threshold_sync
        return await preview(
            self._lifecycle,
            limit=limit,
            max_days=max_days,
            stage_a_threshold=stage_a_threshold,
        )


__all__ = ["SqliteJobsEvaluations"]
