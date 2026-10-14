"""SQLite review interface; source hooks share the enclosing write transaction."""

from __future__ import annotations

from jobfeed.adapters.store._posting_spam import SpamDB
from jobfeed.adapters.store._sqlite_capability_support import _immediate_transaction
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


class SqliteSpam:
    """Expose human review without adding a separate storage lifecycle."""

    _lifecycle: SqliteLifecycle

    async def spam_review(
        self, *, status: str = "pending", limit: int = 50, offset: int = 0
    ) -> dict[str, object]:
        """List source findings for human review.

        Args:
            status: Review tab filter.
            limit: Page size.
            offset: Page offset.

        Returns:
            Findings and matching total.
        """
        async with self._lifecycle.connection() as connection:
            return await SpamDB(connection).review(status, limit, offset)

    async def spam_decide(self, job_id: str, *, scope: str, decision: str) -> None:
        """Persist a reversible review decision.

        Args:
            job_id: Source anchoring the decision.
            scope: Job or exact company.
            decision: Allow, block, or reset.
        """
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            await SpamDB(connection).decide(int(job_id), scope, decision)

    async def spam_audit_page(
        self, *, after: int = 0, limit: int = 100
    ) -> dict[str, int]:
        """Explicitly audit a bounded page of historical sources.

        Args:
            after: Exclusive source ID cursor.
            limit: Maximum sources to inspect in one transaction.

        Returns:
            Processed count and next source ID cursor.
        """
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            db = SpamDB(connection)
            rows = await db.rows(
                "SELECT id FROM jobs WHERE id>? ORDER BY id LIMIT ?", after, limit
            )
            for row in rows:
                await db.assess_source(int(row["id"]))
            return {
                "processed": len(rows),
                "after": int(rows[-1]["id"]) if rows else after,
            }
