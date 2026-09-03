"""Recovery edge contracts for expired SQLite run leases."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jobfeed.adapters.store._sqlite_runs import _get_pipeline_run
from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.support.sqlite_claims_fixtures import (
    _sqlite_timestamp as sqlite_timestamp,
)
from tests.support.sqlite_run_lease_fixtures import NOW as _NOW
from tests.support.sqlite_run_lease_fixtures import OWNER_A as _OWNER_A
from tests.support.sqlite_run_lease_fixtures import OWNER_B as _OWNER_B
from tests.support.sqlite_run_lease_fixtures import _lease_owner, _query_one, _run_state
from tests.support.sqlite_run_lease_fixtures import _run_fixture as _run

_RECOVERED_LEASE_COUNT = 2


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
