"""Recovery edge contracts for expired SQLite run leases."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.support.sqlite_claims_fixtures import (
    _sqlite_timestamp as sqlite_timestamp,
)
from tests.support.sqlite_run_lease_fixtures import NOW as _NOW
from tests.support.sqlite_run_lease_fixtures import OWNER_A as _OWNER_A
from tests.support.sqlite_run_lease_fixtures import OWNER_B as _OWNER_B
from tests.support.sqlite_run_lease_fixtures import _lease_owner, _run_state
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
    assert (
        await leases.recover_expired_run_leases(now=recovered_at)
        == _RECOVERED_LEASE_COUNT
    )
    assert await _run_state(lifecycle, terminal.run_id) == (
        "succeeded",
        sqlite_timestamp(finished_at),
    )
    assert await _run_state(lifecycle, missing.run_id) is None
    assert await _lease_owner(lifecycle, "scan") == (1, None, None)
    assert await _lease_owner(lifecycle, "evaluate") == (1, None, None)
    await lifecycle.close()
