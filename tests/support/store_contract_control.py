"""Backend controls for store behavior contracts that need clock/failure setup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg


class PostgresStoreContractControl:
    """PostgreSQL setup controls paired with the backend-neutral store contract."""

    injected_error = asyncpg.RaiseError

    def __init__(self, dsn: str) -> None:
        """Create controls for the PostgreSQL contract database."""
        self._dsn = dsn

    async def set_history_time(
        self,
        *,
        job_id: str,
        to_status: str,
        occurrence: int,
        changed_at: datetime,
    ) -> None:
        """Set one status occurrence's timestamp in append order.

        Args:
            job_id: Store-assigned job identity.
            to_status: History status whose occurrence should change.
            occurrence: Zero-based occurrence in append order.
            changed_at: Replacement aware timestamp.

        Raises:
            AssertionError: If the requested history occurrence does not exist.
        """
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(
                """SELECT id FROM job_status_history
                   WHERE job_id = $1 AND to_status = $2
                   ORDER BY id ASC LIMIT 1 OFFSET $3""",
                int(job_id),
                to_status,
                occurrence,
            )
            if row is None:
                raise AssertionError(
                    f"missing history occurrence {to_status}[{occurrence}] for {job_id}"
                )
            await conn.execute(
                "UPDATE job_status_history SET changed_at = $1 WHERE id = $2",
                changed_at,
                row["id"],
            )
        finally:
            await conn.close()

    async def set_current_status_time(
        self,
        *,
        job_id: str,
        changed_at: datetime,
    ) -> None:
        """Set the current status clock for decay-boundary tests.

        Args:
            job_id: Store-assigned job identity.
            changed_at: Replacement aware timestamp.

        Raises:
            AssertionError: If the job has no current status row.
        """
        conn = await asyncpg.connect(self._dsn)
        try:
            result = await conn.execute(
                "UPDATE job_status SET last_status_change_at = $1 WHERE job_id = $2",
                changed_at,
                int(job_id),
            )
            if result == "UPDATE 0":
                raise AssertionError(f"missing status row for {job_id}")
        finally:
            await conn.close()

    @asynccontextmanager
    async def reject_ghost_history_inserts(self) -> AsyncIterator[None]:
        """Reject ghosted history inserts until the context exits.

        Returns:
            Async context manager yielding while the rejection trigger is active.
        """
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(
                """CREATE FUNCTION contract_reject_ghost_history_fn()
                   RETURNS TRIGGER AS $$
                   BEGIN
                       RAISE EXCEPTION 'contract injected history failure';
                   END;
                   $$ LANGUAGE plpgsql"""
            )
            await conn.execute(
                """CREATE TRIGGER contract_reject_ghost_history
                   BEFORE INSERT ON job_status_history
                   FOR EACH ROW WHEN (NEW.to_status = 'ghosted')
                   EXECUTE FUNCTION contract_reject_ghost_history_fn()"""
            )
            yield
        finally:
            await conn.execute(
                "DROP TRIGGER IF EXISTS contract_reject_ghost_history "
                "ON job_status_history"
            )
            await conn.execute(
                "DROP FUNCTION IF EXISTS contract_reject_ghost_history_fn()"
            )
            await conn.close()

    @asynccontextmanager
    async def delay_interview_completion_updates(self) -> AsyncIterator[None]:
        """Delay completion updates so concurrent selectors overlap reliably.

        Returns:
            Async context manager yielding while the delay trigger is active.
        """
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(
                """CREATE FUNCTION contract_delay_interview_completion_fn()
                   RETURNS TRIGGER AS $$
                   BEGIN
                       PERFORM pg_sleep(0.2);
                       RETURN NEW;
                   END;
                   $$ LANGUAGE plpgsql"""
            )
            await conn.execute(
                """CREATE TRIGGER contract_delay_interview_completion
                   BEFORE UPDATE OF completed_at ON interview_rounds
                   FOR EACH ROW WHEN (
                       OLD.completed_at IS NULL
                       AND NEW.completed_at IS NOT NULL
                   )
                   EXECUTE FUNCTION contract_delay_interview_completion_fn()"""
            )
            yield
        finally:
            await conn.execute(
                "DROP TRIGGER IF EXISTS contract_delay_interview_completion "
                "ON interview_rounds"
            )
            await conn.execute(
                "DROP FUNCTION IF EXISTS contract_delay_interview_completion_fn()"
            )
            await conn.close()


__all__ = ["PostgresStoreContractControl"]
