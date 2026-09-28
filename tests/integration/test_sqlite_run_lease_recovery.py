"""Recovery edge contracts for expired SQLite run leases."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jobfeed.adapters.store._sqlite_runs import _get_pipeline_run
from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.support.sqlite_claims_fixtures import _seed_evaluation as seed_evaluation
from tests.support.sqlite_claims_fixtures import _seed_job as seed_job
from tests.support.sqlite_claims_fixtures import _sqlite_timestamp as sqlite_timestamp
from tests.support.sqlite_run_lease_fixtures import NOW as _NOW
from tests.support.sqlite_run_lease_fixtures import OWNER_A as _OWNER_A
from tests.support.sqlite_run_lease_fixtures import OWNER_B as _OWNER_B
from tests.support.sqlite_run_lease_fixtures import _lease_owner, _query_one, _run_state
from tests.support.sqlite_run_lease_fixtures import _run_fixture as _run
from tests.support.sqlite_run_lease_fixtures import _terminal_run as terminal_run

_RECOVERED_LEASE_COUNT = 2


async def test_recovery_clears_empty_claims_only_after_evaluation_lease_ends(
    tmp_path: Path,
) -> None:
    """A live scorer keeps its claims; a stopped scorer leaves no empty locks."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    leases = SqliteClaimsRuns(lifecycle)
    run = _run(80)
    assert await leases.start_run_with_lease(
        run, kind="evaluate", owner_id=_OWNER_A, now=_NOW
    )
    async with lifecycle.connection() as connection:
        empty = await seed_job(connection, canonical_id="empty", discovered_at=_NOW)
        scored = await seed_job(connection, canonical_id="scored", discovered_at=_NOW)
        errored = await seed_job(connection, canonical_id="errored", discovered_at=_NOW)
        for job_id in (empty, scored, errored):
            await seed_evaluation(
                connection,
                job_id=job_id,
                updated_at=_NOW - timedelta(days=2),
                stage_a_status="in_progress",
                stage_a_score=80 if job_id == scored else None,
                stage_a_error="failed" if job_id == errored else None,
            )
        cursor = await connection.execute("INSERT INTO real_jobs DEFAULT VALUES")
        real_job_id = cursor.lastrowid
        await cursor.close()
        await connection.execute(
            "INSERT INTO real_job_evaluations "
            "(real_job_id,source_job_id,input_jd_text,input_facts_json,"
            "stage_a_status,updated_at) VALUES (?,?,?,?,?,?)",
            (
                real_job_id,
                empty,
                "Full JD",
                "{}",
                "in_progress",
                sqlite_timestamp(_NOW),
            ),
        )

    assert (
        await leases.recover_expired_run_leases(now=_NOW + timedelta(seconds=1)) == []
    )
    assert await _claim_states(lifecycle) == [
        ("empty", "in_progress"),
        ("errored", "in_progress"),
        ("scored", "in_progress"),
    ]
    assert await _canonical_claim_state(lifecycle) == "in_progress"

    recovered = await leases.recover_expired_run_leases(
        now=_NOW + timedelta(seconds=180)
    )
    assert len(recovered) == 1
    assert await _claim_states(lifecycle) == [
        ("empty", None),
        ("errored", "in_progress"),
        ("scored", "in_progress"),
    ]
    assert await _canonical_claim_state(lifecycle) is None
    await lifecycle.close()


async def _canonical_claim_state(lifecycle: SqliteLifecycle) -> str | None:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT stage_a_status FROM real_job_evaluations LIMIT 1"
        )
        row = await cursor.fetchone()
        await cursor.close()
    return None if row is None else row[0]


async def _claim_states(lifecycle: SqliteLifecycle) -> list[tuple[str, str | None]]:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT j.canonical_id,e.stage_a_status FROM evaluations e "
            "JOIN jobs j ON j.id=e.job_id ORDER BY j.canonical_id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [(str(row[0]), row[1]) for row in rows]


async def test_evaluation_finish_and_stop_release_empty_claims(tmp_path: Path) -> None:
    """Both terminal paths clear unspent claims as soon as work ends."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    leases = SqliteClaimsRuns(lifecycle)
    finished = _run(81)
    assert await leases.start_run_with_lease(
        finished, kind="evaluate", owner_id=_OWNER_A, now=_NOW
    )
    async with lifecycle.connection() as connection:
        first = await seed_job(connection, canonical_id="first", discovered_at=_NOW)
        await seed_evaluation(
            connection, job_id=first, updated_at=_NOW, stage_a_status="in_progress"
        )
    finished_at = _NOW + timedelta(seconds=1)
    assert await leases.finalize_run_with_lease(
        terminal_run(finished, finished_at),
        kind="evaluate",
        owner_id=_OWNER_A,
        generation=1,
        now=finished_at,
    )
    assert await _claim_states(lifecycle) == [("first", None)]

    stopped = _run(82)
    assert await leases.start_run_with_lease(
        stopped, kind="evaluate", owner_id=_OWNER_B, now=finished_at
    )
    async with lifecycle.connection() as connection:
        second = await seed_job(connection, canonical_id="second", discovered_at=_NOW)
        await seed_evaluation(
            connection, job_id=second, updated_at=_NOW, stage_a_status="in_progress"
        )
    assert await leases.stop_pipeline_run(
        stopped.run_id, now=finished_at + timedelta(seconds=1)
    )
    assert await _claim_states(lifecycle) == [("first", None), ("second", None)]
    await lifecycle.close()


async def test_expired_recovery_clears_terminal_and_missing_run_leases(
    tmp_path: Path,
) -> None:
    """Expired lease fields clear even when the old run needs no failure write."""
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    leases = SqliteClaimsRuns(lifecycle)
    terminal = _run(60)
    missing = _run(61)
    assert await leases.start_run_with_lease(
        terminal,
        kind="scan",
        owner_id=_OWNER_A,
        now=_NOW,
    )
    assert await leases.start_run_with_lease(
        missing,
        kind="evaluate",
        owner_id=_OWNER_B,
        now=_NOW,
    )
    finished_at = _NOW + timedelta(seconds=30)
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE pipeline_runs SET status='succeeded', finished_at=? WHERE run_id=?",
            (sqlite_timestamp(finished_at), terminal.run_id),
        )
        await connection.execute(
            "DELETE FROM pipeline_runs WHERE run_id=?",
            (missing.run_id,),
        )

    recovered_at = _NOW + timedelta(seconds=180)
    assert await leases.recover_expired_run_leases(now=recovered_at) == []
    assert await _run_state(lifecycle, terminal.run_id) == (
        "succeeded",
        sqlite_timestamp(finished_at),
    )
    assert await _run_state(lifecycle, missing.run_id) is None
    assert await _lease_owner(lifecycle, "scan") == (1, None, None)
    assert await _lease_owner(lifecycle, "evaluate") == (1, None, None)
    await lifecycle.close()


async def test_expired_recovery_preserves_checkpoint_and_records_reason(
    tmp_path: Path,
) -> None:
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    leases = SqliteClaimsRuns(lifecycle)
    run = _run(70)
    assert await leases.start_run_with_lease(
        run, kind="scan", owner_id=_OWNER_A, now=_NOW
    )
    run.jobs_discovered = 1666
    run.jobs_updated = 1653
    run.scan_stats = {
        "linkedin_guest": {
            "fetched": 1666,
            "discovered": 1666,
            "inserted": 13,
            "updated": 1653,
            "has_jd": 1640,
            "full": 1600,
            "partial": 40,
            "missing": 26,
        }
    }
    assert await leases.checkpoint_run_with_lease(
        run,
        kind="scan",
        owner_id=_OWNER_A,
        generation=1,
        now=_NOW + timedelta(seconds=60),
    )

    recovered_at = _NOW + timedelta(seconds=180)
    recovered = await leases.recover_expired_run_leases(now=recovered_at)
    assert len(recovered) == 1
    stored = await _query_one(
        lifecycle,
        "SELECT jobs_discovered, jobs_updated, failure_code, failure_message, "
        "failed_stage, scan_stats_json FROM pipeline_runs WHERE run_id=?",
        (run.run_id,),
    )
    assert stored == (
        1666,
        1653,
        "interrupted",
        "Run interrupted after its worker stopped responding",
        "scan",
        '{"linkedin_guest":{"discovered":1666,"fetched":1666,"full":1600,'
        '"has_jd":1640,'
        '"inserted":13,"missing":26,"partial":40,"updated":1653}}',
    )
    async with lifecycle.connection() as connection:
        hydrated = await _get_pipeline_run(connection, run.run_id)
    assert hydrated is not None
    assert hydrated.scan_stats == run.scan_stats
    await lifecycle.close()
