"""SQLite pipeline-run leases with monotonic generation fencing."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from jobfeed.adapters.store._run_scan_stats import dump_scan_stats
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
from jobfeed.ports.run_leases import RecoveredRun


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

    async def checkpoint_run_with_lease(
        self,
        run: PipelineRun,
        *,
        kind: str,
        owner_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Persist current counters only while the exact lease is live."""
        now_text = _validate_token(
            kind=kind,
            owner_id=owner_id,
            run_id=run.run_id,
            generation=generation,
            now=now,
        )
        run.last_progress_at = now
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
            cursor = await connection.execute(
                """UPDATE pipeline_runs SET jobs_discovered=?, jobs_inserted=?,
                       jobs_updated=?, jobs_filtered=?, jobs_ml_gated=?,
                       jobs_seniority_filtered=?, jobs_gate_passed=?,
                       stage_a_scored=?, stage_b_scored=?, jobs_scored=?,
                       total_llm_cost_usd=?, errors=?, last_progress_at=?,
                       failed_stage=?, failed_source=?, restart_count=?,
                       scan_stats_json=?
                   WHERE run_id=? AND status='running'""",
                (
                    run.jobs_discovered,
                    run.jobs_inserted,
                    run.jobs_updated,
                    run.jobs_filtered,
                    run.jobs_ml_gated,
                    run.jobs_seniority_filtered,
                    run.jobs_gate_passed,
                    run.stage_a_scored,
                    run.stage_b_scored,
                    run.jobs_scored,
                    run.total_llm_cost_usd,
                    run.errors,
                    now_text,
                    run.progress_stage or run.scan_phase or kind,
                    run.scan_source or run.source,
                    run.restart_count,
                    dump_scan_stats(run.scan_stats),
                    run.run_id,
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

    async def recover_expired_run_leases(self, *, now: datetime) -> list[RecoveredRun]:
        """Fail matching running runs and clear only expired occupied leases."""
        now_text = _require_utc_timestamp(now)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            rows = await _fetch_rows(
                connection,
                """SELECT l.kind, l.generation, l.owner_id, l.run_id,
                          r.source, r.restart_count
                   FROM run_leases AS l
                   LEFT JOIN pipeline_runs AS r ON r.run_id=l.run_id
                   WHERE l.owner_id IS NOT NULL AND l.expires_at<=?
                   ORDER BY l.kind""",
                (now_text,),
            )
            recovered: list[RecoveredRun] = []
            for row in rows:
                interrupted = await _recover_lease(connection, row, now_text)
                if interrupted:
                    recovered.append(
                        RecoveredRun(
                            run_id=str(row["run_id"]),
                            kind=str(row["kind"]),  # type: ignore[arg-type]
                            source=str(row["source"]),
                            restart_count=int(row["restart_count"]),
                        )
                    )
        return recovered

    async def link_restarted_run(self, run_id: str, replacement_run_id: str) -> bool:
        """Link an interrupted attempt once, rejecting duplicate restarts."""
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                """UPDATE pipeline_runs SET restarted_by_run_id=?
                   WHERE run_id=? AND failure_code='interrupted'
                     AND restart_count=0 AND restarted_by_run_id IS NULL""",
                (replacement_run_id, run_id),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed == 1

    async def stop_pipeline_run(self, run_id: str, *, now: datetime) -> bool:
        """Atomically fail one running row and clear its matching lease."""
        now_text = _require_utc_timestamp(now)
        async with (
            self._lifecycle.connection() as connection,
            _immediate_transaction(connection),
        ):
            cursor = await connection.execute(
                """UPDATE pipeline_runs SET status='failed', finished_at=?,
                       failure_code='user_stopped',
                       failure_message='Run stopped by user'
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
