"""Transition-only Stage B threshold synchronizer for PostgreSQL runtime."""

from __future__ import annotations

from jobfeed.ports.store_ext import StoreEvaluationBatchMixin


class LegacyPostgresStageBThresholdSync:
    """Bridge PostgreSQL's two legacy writes during the SQLite cutover.

    PostgreSQL cannot provide the new single-transaction capability without a
    wider adapter rewrite. Keeping this non-atomic behavior in an explicitly
    injected transition adapter prevents service code from silently treating
    every legacy batch store as if it implemented the SQLite guarantee.
    """

    def __init__(self, store: StoreEvaluationBatchMixin) -> None:
        """Wrap the two existing PostgreSQL threshold operations."""
        self._store = store

    async def sync_stage_b_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> tuple[int, int]:
        """Run the bounded PostgreSQL reopen-then-skip compatibility path.

        Args:
            threshold: Minimum Stage A score allowed into Stage B.
            max_days: Optional freshness window applied to both writes.

        Returns:
            Reopened-row count followed by skipped-row count.
        """
        reopened = await self._store.reopen_stage_b_at_or_above_threshold(
            threshold,
            max_days=max_days,
        )
        skipped = await self._store.mark_stage_b_below_threshold(
            threshold,
            max_days=max_days,
        )
        return reopened, skipped


__all__ = ["LegacyPostgresStageBThresholdSync"]
