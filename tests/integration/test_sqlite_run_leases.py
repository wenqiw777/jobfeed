"""Integration contracts for SQLite run-lease generation fencing."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from tests.support.sqlite_claims_fixtures import _sqlite_timestamp as sqlite_timestamp
from tests.support.sqlite_run_lease_fixtures import (
    NOW as _NOW,
)
from tests.support.sqlite_run_lease_fixtures import (
    OWNER_A as _OWNER_A,
)
from tests.support.sqlite_run_lease_fixtures import (
    OWNER_B as _OWNER_B,
)
from tests.support.sqlite_run_lease_fixtures import (
    _lease_expiry,
    _lease_owner,
    _pipeline_count,
    _run_counters,
    _run_state,
)
from tests.support.sqlite_run_lease_fixtures import _run_fixture as _run
from tests.support.sqlite_run_lease_fixtures import _terminal_run as _terminal

_SECOND_GENERATION = 2


async def _open_capability(tmp_path: Path) -> tuple[SqliteLifecycle, SqliteClaimsRuns]:
    lifecycle = SqliteLifecycle(tmp_path / "jobfeed.db", ensure_sqlite_schema)
    await lifecycle.open()
    return lifecycle, SqliteClaimsRuns(lifecycle)


async def test_start_conflict_takeover_and_generation_fencing(tmp_path: Path) -> None:
    """Free start wins, live conflict loses, and expired takeover fences old owner."""
    lifecycle, leases = await _open_capability(tmp_path)
    first = _run(1)
    assert (
        await leases.start_run_with_lease(
            first, kind="evaluate", owner_id=_OWNER_A, now=_NOW
        )
        == 1
    )
    assert (
        await leases.start_run_with_lease(
            _run(2), kind="evaluate", owner_id=_OWNER_B, now=_NOW
        )
        is None
    )

    takeover_now = _NOW + timedelta(seconds=180)
    generation = await leases.start_run_with_lease(
        _run(3), kind="evaluate", owner_id=_OWNER_B, now=takeover_now
    )
    assert generation == _SECOND_GENERATION
    assert await _run_state(lifecycle, first.run_id) == (
        "failed",
        sqlite_timestamp(takeover_now),
    )
    assert not await leases.renew_run_lease(
        kind="evaluate",
        owner_id=_OWNER_A,
        run_id=first.run_id,
        generation=1,
        now=takeover_now,
    )
    assert not await leases.finalize_run_with_lease(
        _terminal(first, takeover_now),
        kind="evaluate",
        owner_id=_OWNER_A,
        generation=1,
        now=takeover_now,
    )
    await lifecycle.close()


async def test_renew_and_finalize_require_live_complete_fencing_token(
    tmp_path: Path,
) -> None:
    """Renew and finalize use strict expiry plus complete generation identity."""
    lifecycle, leases = await _open_capability(tmp_path)
    running = _run(10)
    generation = await leases.start_run_with_lease(
        running, kind="scan", owner_id=_OWNER_A, now=_NOW
    )
    assert generation == 1
    renewed_at = _NOW + timedelta(seconds=30)
    assert await leases.renew_run_lease(
        kind="scan",
        owner_id=_OWNER_A,
        run_id=running.run_id,
        generation=generation,
        now=renewed_at,
    )
    assert await _lease_expiry(lifecycle, "scan") == sqlite_timestamp(
        renewed_at + timedelta(seconds=180)
    )
    assert not await leases.renew_run_lease(
        kind="scan",
        owner_id=_OWNER_A,
        run_id=running.run_id,
        generation=generation + 1,
        now=renewed_at,
    )

    finished_at = renewed_at + timedelta(seconds=1)
    terminal = _terminal(running, finished_at)
    assert await leases.finalize_run_with_lease(
        terminal,
        kind="scan",
        owner_id=_OWNER_A,
        generation=generation,
        now=finished_at,
    )
    assert await _run_counters(lifecycle, running.run_id) == (
        "succeeded",
        terminal.jobs_discovered,
        terminal.jobs_gate_passed,
        terminal.errors,
    )
    assert await _lease_owner(lifecycle, "scan") == (generation, None, None)
    assert not await leases.finalize_run_with_lease(
        terminal,
        kind="scan",
        owner_id=_OWNER_A,
        generation=generation,
        now=finished_at,
    )

    second = _run(11)
    second_generation = await leases.start_run_with_lease(
        second,
        kind="scan",
        owner_id=_OWNER_A,
        now=finished_at,
    )
    assert second_generation == generation + 1
    assert not await leases.renew_run_lease(
        kind="scan",
        owner_id=_OWNER_A,
        run_id=running.run_id,
        generation=generation,
        now=finished_at,
    )
    await lifecycle.close()


async def test_expired_renew_and_finalize_do_not_resurrect_lease(
    tmp_path: Path,
) -> None:
    """The equality boundary is expired for both renewal and finalization."""
    lifecycle, leases = await _open_capability(tmp_path)
    running = _run(20)
    generation = await leases.start_run_with_lease(
        running, kind="evaluate", owner_id=_OWNER_A, now=_NOW
    )
    assert generation == 1
    expires_at = _NOW + timedelta(seconds=180)
    assert not await leases.renew_run_lease(
        kind="evaluate",
        owner_id=_OWNER_A,
        run_id=running.run_id,
        generation=generation,
        now=expires_at,
    )
    assert not await leases.finalize_run_with_lease(
        _terminal(running, expires_at),
        kind="evaluate",
        owner_id=_OWNER_A,
        generation=generation,
        now=expires_at,
    )
    assert await _run_state(lifecycle, running.run_id) == ("running", None)
    await lifecycle.close()


async def test_start_and_finalize_failure_injection_roll_back_both_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failures at both two-table boundaries leave no partial authority."""
    lifecycle, leases = await _open_capability(tmp_path)

    async def fail_start(_connection: aiosqlite.Connection) -> None:
        raise RuntimeError("start boundary")

    monkeypatch.setattr(leases, "_after_start_lease_mutation", fail_start)
    with pytest.raises(RuntimeError, match="start boundary"):
        await leases.start_run_with_lease(
            _run(30), kind="scan", owner_id=_OWNER_A, now=_NOW
        )
    assert await _lease_owner(lifecycle, "scan") == (0, None, None)
    assert await _pipeline_count(lifecycle) == 0

    monkeypatch.undo()
    running = _run(31)
    generation = await leases.start_run_with_lease(
        running, kind="scan", owner_id=_OWNER_A, now=_NOW
    )

    async def fail_finalize(_connection: aiosqlite.Connection) -> None:
        raise RuntimeError("finalize boundary")

    monkeypatch.setattr(leases, "_after_finalize_run_update", fail_finalize)
    with pytest.raises(RuntimeError, match="finalize boundary"):
        await leases.finalize_run_with_lease(
            _terminal(running, _NOW + timedelta(seconds=1)),
            kind="scan",
            owner_id=_OWNER_A,
            generation=generation or 0,
            now=_NOW + timedelta(seconds=1),
        )
    assert await _run_state(lifecycle, running.run_id) == ("running", None)
    assert await _lease_owner(lifecycle, "scan") == (
        generation,
        _OWNER_A,
        running.run_id,
    )
    await lifecycle.close()


async def test_startup_recovery_only_clears_expired_occupied_leases(
    tmp_path: Path,
) -> None:
    """Recovery ignores live rows and retains every lease generation."""
    lifecycle, leases = await _open_capability(tmp_path)
    expired_run = _run(40)
    live_run = _run(41)
    assert await leases.start_run_with_lease(
        expired_run, kind="scan", owner_id=_OWNER_A, now=_NOW
    )
    assert await leases.start_run_with_lease(
        live_run, kind="evaluate", owner_id=_OWNER_B, now=_NOW + timedelta(seconds=60)
    )

    recovered_at = _NOW + timedelta(seconds=180)
    assert len(await leases.recover_expired_run_leases(now=recovered_at)) == 1
    assert await _run_state(lifecycle, expired_run.run_id) == (
        "failed",
        sqlite_timestamp(recovered_at),
    )
    assert await _run_state(lifecycle, live_run.run_id) == ("running", None)
    assert await _lease_owner(lifecycle, "scan") == (1, None, None)
    assert await _lease_owner(lifecycle, "evaluate") == (
        1,
        _OWNER_B,
        live_run.run_id,
    )
    await lifecycle.close()


async def test_stop_pipeline_run_fails_row_and_releases_live_lease(
    tmp_path: Path,
) -> None:
    lifecycle, leases = await _open_capability(tmp_path)
    running = _run(42)
    assert await leases.start_run_with_lease(
        running, kind="scan", owner_id=_OWNER_A, now=_NOW
    )

    stopped_at = _NOW + timedelta(seconds=1)
    assert await leases.stop_pipeline_run(running.run_id, now=stopped_at)
    assert await _run_state(lifecycle, running.run_id) == (
        "failed",
        sqlite_timestamp(stopped_at),
    )
    assert await _lease_owner(lifecycle, "scan") == (1, None, None)
    await lifecycle.close()


@pytest.mark.parametrize(
    ("kind", "owner", "now"),
    [
        ("other", _OWNER_A, _NOW),
        ("scan", "not-a-uuid", _NOW),
        ("scan", _OWNER_A, datetime(2026, 8, 12)),
    ],
)
async def test_run_lease_inputs_fail_before_mutation(
    tmp_path: Path,
    kind: str,
    owner: str,
    now: datetime,
) -> None:
    """Lease kind, UUIDs, status, generation, and aware time are validated."""
    lifecycle, leases = await _open_capability(tmp_path)
    with pytest.raises(ValueError):
        await leases.start_run_with_lease(_run(50), kind=kind, owner_id=owner, now=now)
    assert await _pipeline_count(lifecycle) == 0
    await lifecycle.close()
