"""Behavior contracts for SQLite status aggregates and projections."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from jobfeed.adapters.store.sqlite_status_applications import (
    SqliteStatusApplications,
)
from jobfeed.domain.models import BulkTransitionRequest, StatusFilter, TransitionRequest
from tests.support.sqlite_status_application_fixtures import (
    NOW,
    _open_lifecycle,
    _seed_job,
)

_SECOND_CALL = 2


class _AtTime(SqliteStatusApplications):
    def _application_time(self, value=None):  # type: ignore[no-untyped-def]
        return NOW if value is None else super()._application_time(value)


async def test_transition_status_and_history_are_atomic_and_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    status = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "status")

    assert (
        await status.transition_status(
            TransitionRequest(job_id=job_id, new_status="scored")
        )
        == "scored"
    )
    assert (
        await status.transition_status(
            TransitionRequest(
                job_id=job_id,
                new_status="applied",
                force=True,
                resume_variant=None,
            )
        )
        == "applied"
    )
    info = await status.get_status(job_id)
    assert info is not None
    assert info.status == "applied"
    assert info.next_followup_at == NOW + timedelta(days=7)
    assert await status.get_status_history(job_id) == ["applied", "scored", "new"]

    async def _fail_history(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("history failure")

    monkeypatch.setattr(status, "_after_status_update", _fail_history)
    with pytest.raises(RuntimeError, match="history failure"):
        await status.transition_status(
            TransitionRequest(job_id=job_id, new_status="offer")
        )
    assert (await status.get_status(job_id)).status == "applied"  # type: ignore[union-attr]
    await lifecycle.close()


async def test_followup_note_list_decay_and_attention_use_application_utc(
    tmp_path: Path,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    status = _AtTime(lifecycle)
    job_id = await _seed_job(lifecycle, "attention")
    await status.transition_status(
        TransitionRequest(job_id=job_id, new_status="applied", force=True)
    )
    assert await status.set_followup(job_id=job_id, at=NOW + timedelta(hours=3))
    assert await status.append_note(job_id=job_id, text="Needs REVIEW")

    due = await status.list_statuses(
        StatusFilter(needs_followup=True, notes_contain="review")
    )
    assert [row.job_id for row in due] == [job_id]
    attention = await status.workflow_attention()
    assert [row.job_id for row in attention.follow_up_today] == [job_id]
    assert attention.follow_up_today[0].days_since == 0

    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE job_status SET last_status_change_at=? WHERE job_id=?",
            ("2026-07-01T00:00:00.000000Z", int(job_id)),
        )
    assert (await status.auto_decay()).ghosted == 1
    await lifecycle.close()


async def test_auto_decay_rolls_back_the_whole_sweep_on_history_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    status = _AtTime(lifecycle)
    first = await _seed_job(lifecycle, "decay-a")
    second = await _seed_job(lifecycle, "decay-b", company="Other")
    for job_id in (first, second):
        await status.transition_status(
            TransitionRequest(job_id=job_id, new_status="applied", force=True)
        )
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE job_status SET last_status_change_at=? WHERE job_id IN (?,?)",
            ("2026-07-01T00:00:00.000000Z", int(first), int(second)),
        )

    calls = 0

    async def _fail_second(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == _SECOND_CALL:
            raise RuntimeError("second history failure")

    monkeypatch.setattr(status, "_after_status_update", _fail_second)
    with pytest.raises(RuntimeError, match="second history failure"):
        await status.auto_decay()
    assert (await status.get_status(first)).status == "applied"  # type: ignore[union-attr]
    assert (await status.get_status(second)).status == "applied"  # type: ignore[union-attr]
    await lifecycle.close()


async def test_bulk_twin_cluster_is_atomic_and_other_clusters_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = await _open_lifecycle(tmp_path)
    status = _AtTime(lifecycle)
    first = await _seed_job(lifecycle, "twin-a")
    twin = await _seed_job(lifecycle, "twin-b")
    other = await _seed_job(lifecycle, "other", company="Other")
    for job_id in (first, twin, other):
        await status.transition_status(
            TransitionRequest(job_id=job_id, new_status="scored")
        )

    async def _fail_one(job_id: int, *_args: object, **_kwargs: object) -> None:
        if job_id == int(twin):
            raise RuntimeError("twin failure")

    monkeypatch.setattr(status, "_before_bulk_member", _fail_one)
    result = await status.transition_status_bulk(
        BulkTransitionRequest(
            items=[(first, "shortlisted"), (other, "shortlisted")],
            reason_selected="bulk",
            reason_cascade="bulk-cascade",
        )
    )
    assert result.succeeded == 1
    assert result.failed == [(first, "twin failure")]
    assert (await status.get_status(first)).status == "scored"  # type: ignore[union-attr]
    assert (await status.get_status(twin)).status == "scored"  # type: ignore[union-attr]
    assert (await status.get_status(other)).status == "shortlisted"  # type: ignore[union-attr]
    await lifecycle.close()
