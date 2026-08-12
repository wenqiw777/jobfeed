"""Cross-process SQLite contention and WAL reader contracts."""

from __future__ import annotations

import multiprocessing
import time
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty
from typing import Any

from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.support.sqlite_claims_fixtures import (
    _seed_evaluation as seed_evaluation,
)
from tests.support.sqlite_claims_fixtures import (
    _seed_job as seed_job,
)
from tests.support.sqlite_claims_process import (
    _claim_worker_process as claim_worker,
)
from tests.support.sqlite_claims_process import (
    _hold_uncommitted_writer as hold_uncommitted_writer,
)
from tests.support.sqlite_claims_process import (
    _run_lease_worker_process as run_lease_worker,
)
from tests.support.sqlite_claims_process import (
    _stage_b_claim_worker_process as stage_b_claim_worker,
)

_NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)
_ROUNDS = 100
_READER_MAX_SECONDS = 2


async def test_two_processes_claim_each_job_at_most_once(tmp_path: Path) -> None:
    """Independent processes produce a disjoint exhaustive Stage A claim."""
    database = tmp_path / "jobfeed.db"
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        expected = {
            await seed_job(
                connection,
                canonical_id=f"process-{index}",
                discovered_at=_NOW,
            )
            for index in range(20)
        }
    await lifecycle.close()

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(target=claim_worker, args=(str(database), barrier, output))
        for _ in range(2)
    ]
    _run_processes(processes)
    claimed = [_queue_get(output), _queue_get(output)]
    flattened = [job_id for result in claimed for job_id in result]

    assert set(flattened) == expected
    assert len(flattened) == len(set(flattened))


async def test_two_processes_claim_each_stage_b_job_at_most_once(
    tmp_path: Path,
) -> None:
    """Independent processes produce a disjoint exhaustive Stage B claim."""
    database = tmp_path / "jobfeed.db"
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        expected: set[str] = set()
        for index in range(20):
            job_id = await seed_job(
                connection,
                canonical_id=f"stage-b-process-{index}",
                discovered_at=_NOW,
            )
            await seed_evaluation(
                connection,
                job_id=job_id,
                updated_at=_NOW,
                stage_a_status="completed",
                stage_a_score=80,
            )
            expected.add(job_id)
    await lifecycle.close()

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(
            target=stage_b_claim_worker,
            args=(str(database), barrier, output),
        )
        for _ in range(2)
    ]
    _run_processes(processes)
    claimed = [_queue_get(output), _queue_get(output)]
    flattened = [job_id for result in claimed for job_id in result]

    assert set(flattened) == expected
    assert len(flattened) == len(set(flattened))


async def test_two_processes_race_one_hundred_fenced_run_rounds(
    tmp_path: Path,
) -> None:
    """Exactly one same-kind winner emerges per round; different kinds both win."""
    database = tmp_path / "jobfeed.db"
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    await lifecycle.close()

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(
            target=run_lease_worker,
            args=(str(database), worker, _ROUNDS, barrier, output),
        )
        for worker in range(2)
    ]
    _run_processes(processes, timeout=45)
    first_wins, first_distinct = _queue_get(output)
    second_wins, second_distinct = _queue_get(output)

    assert len(first_wins) == _ROUNDS
    assert all(a ^ b for a, b in zip(first_wins, second_wins, strict=True))
    assert first_distinct and second_distinct

    await lifecycle.open()
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT kind, generation FROM run_leases ORDER BY kind"
        )
        generations = dict(await cursor.fetchall())
        await cursor.close()
    assert generations == {"evaluate": 51, "scan": 51}
    await lifecycle.close()


async def test_reader_observes_committed_snapshot_during_writer(
    tmp_path: Path,
) -> None:
    """A preview completes against committed state while another process writes."""
    database = tmp_path / "jobfeed.db"
    lifecycle = SqliteLifecycle(database, ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        job_id = await seed_job(
            connection,
            canonical_id="reader",
            discovered_at=_NOW,
        )
        await seed_evaluation(
            connection,
            job_id=job_id,
            updated_at=_NOW,
            stage_a_status="error",
            stage_a_error="retry",
        )
    claims = SqliteClaimsRuns(lifecycle)

    context = multiprocessing.get_context("spawn")
    started = context.Event()
    release = context.Event()
    writer = context.Process(
        target=hold_uncommitted_writer,
        args=(str(database), int(job_id), started, release),
    )
    writer.start()
    assert started.wait(10)
    began = time.monotonic()
    try:
        preview = await claims.preview_claimable_stage_a(now=_NOW, corpus="failed")
    finally:
        release.set()
    elapsed = time.monotonic() - began
    writer.join(10)

    assert writer.exitcode == 0
    assert [job.id for job in preview] == [job_id]
    assert elapsed < _READER_MAX_SECONDS
    await lifecycle.close()


def _run_processes(
    processes: list[multiprocessing.Process],
    *,
    timeout: int = 20,
) -> None:
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout)
    assert [process.exitcode for process in processes] == [0, 0]


def _queue_get(output: Any) -> Any:
    try:
        return output.get(timeout=5)
    except Empty as error:
        raise AssertionError("worker did not publish a result") from error
