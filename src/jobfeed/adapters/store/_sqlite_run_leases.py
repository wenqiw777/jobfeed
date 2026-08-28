"""SQLite pipeline-run leases with monotonic generation fencing."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _immediate_transaction,
    _require_utc_timestamp,
)
from jobfeed.adapters.store._sqlite_run_lease_support import (
    _LEASE_TTL,
    _fail_expired_run,
    _insert_run,
    _is_claimable,
    _lease_row,
    _recover_lease,
    _update_terminal_run,
    _validate_finalize,
    _validate_start,
    _validate_token,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models_run import PipelineRun


class _SqliteRunLeases:
    """Implement atomic run insertion, heartbeat, finalization, and recovery."""

    _lifecycle: SqliteLifecycle

    async def start_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: str,
        owner_id: str,
        now: datetime,
    ) -> int | None:
        """Atomically acquire one kind and insert its complete running run."""
        now_text = _validate_start(run, kind=kind, owner_id=owner_id, now=now)
        expires_text = _require_utc_timestamp(now + _LEASE_TTL)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            lease = await _lease_row(connection, kind)
            if not _is_claimable(lease, now_text):
                return None
            old_run_id = lease["run_id"]
            if old_run_id is not None:
                await _fail_expired_run(
                    connection,
                    run_id=str(old_run_id),
                    finished_at=now_text,
                    kind=kind,
                )
            generation = int(lease["generation"]) + 1
            await connection.execute(
                """UPDATE run_leases SET generation=?, owner_id=?, run_id=?,
                       heartbeat_at=?, expires_at=? WHERE kind=?""",
                (generation, owner_id, run.run_id, now_text, expires_text, kind),
            )
            await self._after_start_lease_mutation(connection)
            await _insert_run(connection, run)
        return generation

    async def renew_run_lease(
        self,
        *,
        kind: str,
        owner_id: str,
        run_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Extend a live lease only when the complete fencing token matches."""
        now_text = _validate_token(
            kind=kind,
            owner_id=owner_id,
            run_id=run_id,
            generation=generation,
            now=now,
        )
        expires_text = _require_utc_timestamp(now + _LEASE_TTL)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            cursor = await connection.execute(
                """UPDATE run_leases SET heartbeat_at=?, expires_at=?
                   WHERE kind=? AND owner_id=? AND run_id=? AND generation=?
                     AND expires_at>?""",
                (
                    now_text,
                    expires_text,
                    kind,
                    owner_id,
                    run_id,
                    generation,
                    now_text,
                ),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed == 1

    async def finalize_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: str,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Atomically persist a terminal snapshot and clear a live lease."""
        now_text = _validate_finalize(
            run,
            kind=kind,
            owner_id=owner_id,
            generation=generation,
            now=now,
        )
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            lease = await _fetch_row(
                connection,
                """SELECT 1 FROM run_leases
                   WHERE kind=? AND owner_id=? AND run_id=? AND generation=?
                     AND expires_at>?""",
                (kind, owner_id, run.run_id, generation, now_text),
            )
            if lease is None:
                return False
            if not await _update_terminal_run(connection, run):
                return False
            await self._after_finalize_run_update(connection)
            cursor = await connection.execute(
                """UPDATE run_leases SET owner_id=NULL, run_id=NULL,
                       heartbeat_at=NULL, expires_at=NULL
                   WHERE kind=? AND owner_id=? AND run_id=? AND generation=?
                     AND expires_at>?""",
                (kind, owner_id, run.run_id, generation, now_text),
            )
            cleared = cursor.rowcount
            await cursor.close()
            if cleared != 1:
                raise RuntimeError("live run lease changed inside transaction")
        return True

    async def recover_expired_run_leases(self, *, now: datetime) -> int:
        """Fail matching running runs and clear only expired occupied leases."""
        now_text = _require_utc_timestamp(now)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            rows = await _fetch_rows(
                connection,
                """SELECT kind, generation, owner_id, run_id
                   FROM run_leases
                   WHERE owner_id IS NOT NULL AND expires_at<=?
                   ORDER BY kind""",
                (now_text,),
            )
            for row in rows:
                await _recover_lease(connection, row, now_text)
        return len(rows)

    async def stop_pipeline_run(self, run_id: str, *, now: datetime) -> bool:
        """Atomically fail one running row and clear its matching lease."""
        now_text = _require_utc_timestamp(now)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            cursor = await connection.execute(
                """UPDATE pipeline_runs SET status='failed', finished_at=?
                   WHERE run_id=? AND status='running'""",
                (now_text, run_id),
            )
            stopped = cursor.rowcount == 1
            await cursor.close()
            if not stopped:
                return False
            await connection.execute(
                """UPDATE run_leases SET owner_id=NULL, run_id=NULL,
                       heartbeat_at=NULL, expires_at=NULL
                   WHERE run_id=?""",
                (run_id,),
            )
        return True

    async def _after_start_lease_mutation(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        del connection

    async def _after_finalize_run_update(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        del connection
