"""Atomic SQLite review decisions using the existing identity merge machinery."""

from __future__ import annotations

import aiosqlite

from jobfeed.adapters.store._identity_review import IdentityReviewDB
from jobfeed.adapters.store._sqlite_capability_support import _immediate_transaction
from jobfeed.adapters.store._sqlite_real_job_identity import _merge
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.filtering import HardFilters


class SqliteIdentityReview:
    """Expose duplicate review and preserved decisions."""

    _lifecycle: SqliteLifecycle

    async def identity_review(
        self,
        *,
        status: str,
        limit: int,
        offset: int,
        hard_filters: HardFilters | None = None,
    ) -> dict[str, object]:
        """Read one review page.

        Args:
            status: Pending or reviewed.
            limit: Page size.
            offset: Page offset.
            hard_filters: Current Results visibility policy.

        Returns:
            Cards and total.
        """
        async with self._lifecycle.connection() as db:
            return await IdentityReviewDB(db).review(
                status, limit, offset, hard_filters
            )

    async def identity_decide(
        self,
        left_id: str,
        right_id: str,
        *,
        decision: str,
        target_id: str | None = None,
    ) -> None:
        """Apply one explicit duplicate decision atomically.

        Args:
            left_id: First identity.
            right_id: Second identity.
            decision: Separate or merge.
            target_id: Chosen surviving identity.
        """
        left, right = int(left_id), int(right_id)
        target = int(target_id) if target_id else None
        async with self._lifecycle.connection() as db, _immediate_transaction(db):
            db.row_factory = aiosqlite.Row
            review = IdentityReviewDB(db)
            snapshot = await review.prepare(left, right, decision, target)
            if decision == "merge":
                assert target is not None
                await _merge(db, target, right if target == left else left)
            await review.finish(left, right, snapshot)
