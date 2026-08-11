"""Independent-process PostgreSQL worker harness for store concurrency tests."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from jobfeed.adapters.store.postgres import PostgresStore
from tests.support.factories import make_job

PROCESS_START_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.01


async def _wait_for_path(path: Path) -> None:
    while not path.exists():
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def wait_for_process_signal(path: Path) -> None:
    """Wait until a worker creates its synchronization path."""
    await asyncio.wait_for(
        _wait_for_path(path),
        timeout=PROCESS_START_TIMEOUT_SECONDS,
    )


async def _hold_stage_a_write(
    store: PostgresStore,
    payload: dict[str, Any],
) -> dict[str, object]:
    entered_path = Path(payload["entered_path"])
    release_path = Path(payload["release_path"])
    pool = store._get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE evaluations
                  SET stage_a_status = 'in_progress', updated_at = now()
                WHERE job_id = $1""",
            int(payload["job_id"]),
        )
        entered_path.touch()
        await _wait_for_path(release_path)
    return {"held_job_id": payload["job_id"]}


async def _run_operation(
    store: PostgresStore,
    payload: dict[str, Any],
) -> dict[str, object]:
    operation = payload["operation"]
    if operation == "save_job":
        result = await store.save_job(
            make_job(
                payload["canonical_id"],
                company=payload["company"],
            )
        )
        return {
            "job_id": result.job_id,
            "inserted": result.inserted,
            "updated": result.updated,
        }
    if operation == "claim_stage_a":
        jobs = await store.claim_pending_stage_a(
            corpus=payload.get("corpus", "unrated"),
            limit=payload["limit"],
        )
        return {"job_ids": [job.id for job in jobs]}
    if operation == "claim_stage_b":
        jobs = await store.claim_pending_stage_b(limit=payload["limit"])
        return {"job_ids": [job.id for job in jobs]}
    if operation == "hold_stage_a_write":
        return await _hold_stage_a_write(store, payload)
    raise ValueError(f"Unsupported worker operation: {operation}")


async def _worker(
    dsn: str,
    payload: dict[str, Any],
    ready_path: Path,
    start_path: Path,
) -> None:
    store = PostgresStore(dsn)
    await store.connect()
    try:
        ready_path.touch()
        await _wait_for_path(start_path)
        result = await _run_operation(store, payload)
        print(json.dumps(result), flush=True)
    finally:
        await store.close()


async def run_store_process_race(
    *,
    dsn: str,
    payloads: list[dict[str, Any]],
    sync_dir: Path,
) -> list[dict[str, object]]:
    """Start workers, release them together, and return their JSON results."""
    sync_dir.mkdir()
    start_path = sync_dir / "start"
    workers: list[asyncio.subprocess.Process] = []
    ready_paths: list[Path] = []
    for index, payload in enumerate(payloads):
        ready_path = sync_dir / f"ready-{index}"
        ready_paths.append(ready_path)
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.support.pg_process_race",
            "--dsn",
            dsn,
            "--payload",
            json.dumps(payload),
            "--ready-path",
            str(ready_path),
            "--start-path",
            str(start_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        workers.append(worker)
    await asyncio.gather(*(wait_for_process_signal(path) for path in ready_paths))
    start_path.touch()
    outputs = await asyncio.gather(*(worker.communicate() for worker in workers))
    results: list[dict[str, object]] = []
    for worker, (stdout, stderr) in zip(workers, outputs, strict=True):
        if worker.returncode != 0:
            detail = stderr.decode().strip()
            raise RuntimeError(f"PostgreSQL race worker failed: {detail}")
        results.append(json.loads(stdout))
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--ready-path", type=Path, required=True)
    parser.add_argument("--start-path", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """Run one synchronized subprocess worker."""
    args = _parse_args()
    asyncio.run(
        _worker(
            args.dsn,
            json.loads(args.payload),
            args.ready_path,
            args.start_path,
        )
    )


if __name__ == "__main__":
    main()
