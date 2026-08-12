"""Behavior contracts for SQLite interview round aggregates."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from jobfeed.adapters.store.sqlite_status_applications import (
    SqliteStatusApplications,
)
from tests.support.sqlite_status_application_fixtures import (
    NOW,
    _open_lifecycle,
    _seed_job,
)


class _AtTime(SqliteStatusApplications):
    def _application_time(self, value=None):  # type: ignore[no-untyped-def]
        return NOW if value is None else super()._application_time(value)


async def test_round_index_order_nullable_schedule_and_clock_bump(
    tmp_path: Path,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    interviews = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "rounds")
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE job_status SET last_status_change_at=? WHERE job_id=?",
            ("2026-08-01T00:00:00.000000Z", int(job_id)),
        )

    first = await interviews.add_interview_round(job_id=job_id, label="Phone")
    second = await interviews.add_interview_round(
        job_id=job_id,
        label="Technical",
        scheduled_at=NOW + timedelta(days=2),
    )
    assert (first.round_index, first.scheduled_at) == (1, None)
    assert (second.round_index, second.scheduled_at) == (
        2,
        NOW + timedelta(days=2),
    )
    rounds = await interviews.list_interview_rounds(job_id)
    assert [row.round_index for row in rounds] == [1, 2]
    assert (await interviews.get_status(job_id)).last_status_change_at == NOW  # type: ignore[union-attr]
    await lifecycle.close()


async def test_add_and_complete_roll_back_when_clock_update_path_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    interviews = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "rollback")

    async def _fail(operation: str, *_args: object) -> None:
        if operation == "add":
            raise RuntimeError("clock failure")

    monkeypatch.setattr(interviews, "_after_round_mutation", _fail)
    with pytest.raises(RuntimeError, match="clock failure"):
        await interviews.add_interview_round(job_id=job_id, label="Phone")
    assert await interviews.list_interview_rounds(job_id) == []

    monkeypatch.undo()
    interviews = _AtTime(lifecycle)
    await interviews.add_interview_round(job_id=job_id, label="Phone")

    async def _fail_complete(operation: str, *_args: object) -> None:
        if operation == "complete":
            raise RuntimeError("clock failure")

    monkeypatch.setattr(interviews, "_after_round_mutation", _fail_complete)
    with pytest.raises(RuntimeError, match="clock failure"):
        await interviews.complete_interview_round(job_id=job_id, notes="done")
    assert (await interviews.list_interview_rounds(job_id))[0].completed_at is None
    await lifecycle.close()


async def test_concurrent_adds_are_unique_and_monotonic(tmp_path: Path) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    job_id = await _seed_job(lifecycle, "concurrent-add")
    interviews = _AtTime(lifecycle)

    await asyncio.gather(
        *(
            interviews.add_interview_round(job_id=job_id, label=f"Round {index}")
            for index in range(8)
        )
    )

    rounds = await interviews.list_interview_rounds(job_id)
    assert [row.round_index for row in rounds] == list(range(1, 9))
    await lifecycle.close()


async def test_concurrent_completion_has_exactly_one_winner_and_notes_semantics(
    tmp_path: Path,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    job_id = await _seed_job(lifecycle, "concurrent-complete")
    interviews = _AtTime(lifecycle)
    await interviews.add_interview_round(job_id=job_id, label="Phone")

    results = await asyncio.gather(
        interviews.complete_interview_round(job_id=job_id, notes="winner"),
        interviews.complete_interview_round(job_id=job_id, notes="loser"),
        return_exceptions=True,
    )
    winners = [result for result in results if not isinstance(result, Exception)]
    errors = [result for result in results if isinstance(result, ValueError)]
    assert len(winners) == 1
    assert len(errors) == 1
    completed = (await interviews.list_interview_rounds(job_id))[0]
    assert completed.completed_at == NOW
    assert completed.notes in {"winner", "loser"}
    with pytest.raises(ValueError, match="no open interview round"):
        await interviews.complete_interview_round(job_id=job_id)
    await lifecycle.close()
