"""Spawn-safe workers for SQLite claim and run-lease contention tests."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.models_run import PipelineRun

_BASE_TIME = datetime(2026, 8, 12, 12, tzinfo=UTC)


def _claim_worker_process(database: str, barrier: Any, output: Any) -> None:
    """Race one Stage A claim and publish claimed job identities."""
    asyncio.run(_claim_worker(Path(database), barrier, output))


async def _claim_worker(database: Path, barrier: Any, output: Any) -> None:
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    capability = SqliteClaimsRuns(lifecycle)
    barrier.wait()
    claimed = await capability.claim_pending_stage_a(now=_BASE_TIME, limit=1_000)
    output.put([job.id for job in claimed])
    await lifecycle.close()


def _stage_b_claim_worker_process(database: str, barrier: Any, output: Any) -> None:
    """Race one Stage B claim and publish claimed job identities."""
    asyncio.run(_stage_b_claim_worker(Path(database), barrier, output))


async def _stage_b_claim_worker(
    database: Path,
    barrier: Any,
    output: Any,
) -> None:
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    capability = SqliteClaimsRuns(lifecycle)
    barrier.wait()
    claimed = await capability.claim_pending_stage_b(now=_BASE_TIME, limit=1_000)
    output.put([job.id for job in claimed])
    await lifecycle.close()


def _run_lease_worker_process(
    database: str,
    worker: int,
    rounds: int,
    barrier: Any,
    output: Any,
) -> None:
    """Race repeated fenced starts and a final independent-kind acquisition."""
    asyncio.run(_run_lease_worker(Path(database), worker, rounds, barrier, output))


async def _run_lease_worker(
    database: Path,
    worker: int,
    rounds: int,
    barrier: Any,
    output: Any,
) -> None:
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    capability = SqliteClaimsRuns(lifecycle)
    owner_id = str(uuid5(NAMESPACE_URL, f"jobfeed-worker-{worker}"))
    wins: list[bool] = []
    for round_index in range(rounds):
        kind = "scan" if round_index % 2 == 0 else "evaluate"
        now = _BASE_TIME + timedelta(minutes=round_index * 10)
        run = _process_run(worker, round_index, now)
        barrier.wait()
        generation = await capability.start_run_with_lease(
            run,
            kind=kind,
            owner_id=owner_id,
            now=now,
        )
        wins.append(generation is not None)
        barrier.wait()
        if generation is not None:
            finished = now + timedelta(seconds=1)
            assert await capability.finalize_run_with_lease(
                _terminal(run, finished),
                kind=kind,
                owner_id=owner_id,
                generation=generation,
                now=finished,
            )
        barrier.wait()

    distinct_kind = "scan" if worker == 0 else "evaluate"
    distinct_now = _BASE_TIME + timedelta(days=rounds)
    distinct_run = _process_run(worker, rounds, distinct_now)
    barrier.wait()
    distinct_generation = await capability.start_run_with_lease(
        distinct_run,
        kind=distinct_kind,
        owner_id=owner_id,
        now=distinct_now,
    )
    output.put((wins, distinct_generation is not None))
    await lifecycle.close()


def _hold_uncommitted_writer(
    database: str,
    job_id: int,
    started: Any,
    release: Any,
) -> None:
    """Hold one uncommitted write while another process reads the WAL snapshot."""
    connection = sqlite3.connect(database, timeout=5, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE evaluations SET stage_a_status='in_progress' WHERE job_id=?",
            (job_id,),
        )
        started.set()
        if not release.wait(15):
            raise TimeoutError("reader did not release writer")
        connection.rollback()
    finally:
        connection.close()


def _process_run(worker: int, round_index: int, now: datetime) -> PipelineRun:
    return PipelineRun(
        run_id=str(uuid5(NAMESPACE_URL, f"jobfeed-run-{worker}-{round_index}")),
        started_at=now,
        source="process-test",
    )


def _terminal(run: PipelineRun, finished_at: datetime) -> PipelineRun:
    return PipelineRun(
        **(vars(run) | {"status": "succeeded", "finished_at": finished_at})
    )
