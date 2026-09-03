"""Validation and SQL helpers for SQLite run-lease generation fencing."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

import aiosqlite
import structlog

from jobfeed.adapters.store._run_scan_stats import dump_scan_stats
from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _require_utc_timestamp,
    _validate_canonical_uuid,
)
from jobfeed.domain.models_run import PipelineRun

_LEASE_TTL: Final = timedelta(seconds=180)
_RUN_KINDS: Final = frozenset({"scan", "evaluate"})
_TERMINAL_STATUSES: Final = frozenset({"succeeded", "failed"})
_STALE_REASON: Final = "expired_run_lease"
_LOG = structlog.get_logger(__name__)


def _validate_start(
    run: PipelineRun,
    *,
    kind: str,
    owner_id: str,
    now: datetime,
) -> str:
    now_text = _validate_identity(kind, owner_id, run.run_id, now)
    _require_utc_timestamp(run.started_at, "run.started_at")
    if run.status != "running" or run.finished_at is not None:
        raise ValueError("leased run start requires running status and no finish time")
    return now_text


def _validate_token(
    *,
    kind: str,
    owner_id: str,
    run_id: str,
    generation: int,
    now: datetime,
) -> str:
    now_text = _validate_identity(kind, owner_id, run_id, now)
    if isinstance(generation, bool) or generation <= 0:
        raise ValueError("generation must be a positive integer")
    return now_text


def _validate_finalize(
    run: PipelineRun,
    *,
    kind: str,
    owner_id: str,
    generation: int,
    now: datetime,
) -> str:
    now_text = _validate_token(
        kind=kind,
        owner_id=owner_id,
        run_id=run.run_id,
        generation=generation,
        now=now,
    )
    _require_utc_timestamp(run.started_at, "run.started_at")
    if run.status not in _TERMINAL_STATUSES or run.finished_at is None:
        raise ValueError("finalized run requires a succeeded or failed finish")
    _require_utc_timestamp(run.finished_at, "run.finished_at")
    if run.finished_at > now:
        raise ValueError("run.finished_at cannot be later than now")
    return now_text


def _validate_identity(kind: str, owner_id: str, run_id: str, now: datetime) -> str:
    if kind not in _RUN_KINDS:
        raise ValueError(f"unsupported run lease kind: {kind!r}")
    _validate_canonical_uuid(owner_id, "owner_id")
    _validate_canonical_uuid(run_id, "run_id")
    return _require_utc_timestamp(now)


async def _lease_row(
    connection: aiosqlite.Connection,
    kind: str,
) -> aiosqlite.Row:
    row = await _fetch_row(
        connection,
        "SELECT * FROM run_leases WHERE kind=?",
        (kind,),
    )
    if row is None:
        raise RuntimeError(f"missing permanent run lease row: {kind}")
    return row


def _is_claimable(lease: aiosqlite.Row, now_text: str) -> bool:
    if lease["owner_id"] is None:
        return True
    return str(lease["expires_at"]) <= now_text


async def _insert_run(
    connection: aiosqlite.Connection,
    run: PipelineRun,
) -> None:
    await connection.execute(
        """INSERT INTO pipeline_runs (
               run_id, started_at, source, status, jobs_discovered,
               jobs_inserted, jobs_updated, jobs_filtered, jobs_ml_gated,
               jobs_seniority_filtered, jobs_gate_passed, stage_a_scored,
               stage_b_scored, jobs_scored,
               total_llm_cost_usd, errors, finished_at, failure_code,
               failure_message, failed_stage, failed_source, last_progress_at,
               restart_count, restarted_by_run_id, scan_stats_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        _run_values(run),
    )


async def _update_terminal_run(
    connection: aiosqlite.Connection,
    run: PipelineRun,
) -> bool:
    finished_at = run.finished_at
    if finished_at is None:
        raise ValueError("terminal run is missing finished_at")
    cursor = await connection.execute(
        """UPDATE pipeline_runs SET
               status=?, finished_at=?, jobs_discovered=?, jobs_inserted=?,
               jobs_updated=?, jobs_filtered=?, jobs_ml_gated=?,
               jobs_seniority_filtered=?, jobs_gate_passed=?,
               stage_a_scored=?, stage_b_scored=?,
               jobs_scored=?, total_llm_cost_usd=?, errors=?
               , failure_code=?, failure_message=?, failed_stage=?,
               failed_source=?, last_progress_at=?, restart_count=?,
               restarted_by_run_id=?, scan_stats_json=?
           WHERE run_id=? AND status='running'""",
        (
            run.status,
            _require_utc_timestamp(finished_at, "run.finished_at"),
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
            run.failure_code,
            run.failure_message,
            run.failed_stage,
            run.failed_source,
            _require_utc_timestamp(run.last_progress_at, "run.last_progress_at")
            if run.last_progress_at is not None
            else None,
            run.restart_count,
            run.restarted_by_run_id,
            dump_scan_stats(run.scan_stats),
            run.run_id,
        ),
    )
    changed = cursor.rowcount
    await cursor.close()
    return changed == 1


def _run_values(run: PipelineRun) -> tuple[object, ...]:
    return (
        run.run_id,
        _require_utc_timestamp(run.started_at, "run.started_at"),
        run.source,
        run.status,
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
        _require_utc_timestamp(run.finished_at, "run.finished_at")
        if run.finished_at is not None
        else None,
        run.failure_code,
        run.failure_message,
        run.failed_stage,
        run.failed_source,
        _require_utc_timestamp(run.last_progress_at, "run.last_progress_at")
        if run.last_progress_at is not None
        else None,
        run.restart_count,
        run.restarted_by_run_id,
        dump_scan_stats(run.scan_stats),
    )


async def _fail_expired_run(
    connection: aiosqlite.Connection,
    *,
    run_id: str,
    finished_at: str,
    kind: str,
) -> bool:
    cursor = await connection.execute(
        "UPDATE pipeline_runs SET status='failed', finished_at=?, "
        "failure_code='interrupted', "
        "failure_message='Run interrupted after its worker stopped responding', "
        "failed_stage=COALESCE(failed_stage, ?), "
        "failed_source=COALESCE(failed_source, source) "
        "WHERE run_id=? AND status='running'",
        (finished_at, kind, run_id),
    )
    changed = cursor.rowcount
    await cursor.close()
    if changed:
        _LOG.warning(
            "sqlite_run_lease_recovered",
            reason=_STALE_REASON,
            kind=kind,
            run_id=run_id,
        )
    return changed == 1


async def _recover_lease(
    connection: aiosqlite.Connection,
    row: aiosqlite.Row,
    now_text: str,
) -> bool:
    run_id = str(row["run_id"])
    interrupted = await _fail_expired_run(
        connection,
        run_id=run_id,
        finished_at=now_text,
        kind=str(row["kind"]),
    )
    cursor = await connection.execute(
        """UPDATE run_leases SET owner_id=NULL, run_id=NULL,
               heartbeat_at=NULL, expires_at=NULL
           WHERE kind=? AND generation=? AND owner_id=? AND run_id=?
             AND expires_at<=?""",
        (
            row["kind"],
            row["generation"],
            row["owner_id"],
            row["run_id"],
            now_text,
        ),
    )
    changed = cursor.rowcount
    await cursor.close()
    if changed != 1:
        raise RuntimeError("expired run lease changed inside transaction")
    return interrupted
