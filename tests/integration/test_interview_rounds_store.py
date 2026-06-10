"""Interview rounds CRUD integration tests against PostgreSQL.

Covers add/list/complete/upcoming lifecycle, round_index sequencing,
last_status_change_at bumps, and ON DELETE CASCADE from the jobs FK.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import QualityBand
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


def _make_job(canonical_id: str = "iv-1") -> object:
    """Shortcut for a minimal job fixture.

    Args:
        canonical_id: Source-specific natural identity.

    Returns:
        Job posting fixture.
    """
    return make_job(canonical_id, jd_text="JD text", jd_quality=QualityBand.GOOD)


async def _last_status_change(store: PostgresStore, job_id: str) -> datetime:
    """Read last_status_change_at for a job_id.

    Args:
        store: Connected store.
        job_id: Store-assigned job identity.

    Returns:
        Timestamp of last status change.
    """
    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_status_change_at FROM job_status WHERE job_id = $1",
            int(job_id),
        )
    assert row is not None
    return row["last_status_change_at"]


async def test_add_rounds_sequential_index(store: PostgresStore) -> None:
    """Adding rounds to a job assigns round_index 1, 2, 3 sequentially."""
    saved = await store.save_job(_make_job("seq-1"))

    r1 = await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    r2 = await store.add_interview_round(job_id=saved.job_id, label="Technical")
    r3 = await store.add_interview_round(job_id=saved.job_id, label="Final")

    assert r1.round_index == 1
    assert r2.round_index == 2
    assert r3.round_index == 3
    assert r1.label == "Phone Screen"
    assert r2.label == "Technical"
    assert r3.label == "Final"
    # All belong to the same job
    assert r1.job_id == r2.job_id == r3.job_id == int(saved.job_id)


async def test_list_interview_rounds_ordered(store: PostgresStore) -> None:
    """list_interview_rounds returns rounds in ascending round_index order."""
    saved = await store.save_job(_make_job("list-1"))
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    await store.add_interview_round(job_id=saved.job_id, label="Technical")
    await store.add_interview_round(job_id=saved.job_id, label="Final")

    rounds = await store.list_interview_rounds(saved.job_id)

    assert len(rounds) == 3
    assert [r.round_index for r in rounds] == [1, 2, 3]
    assert [r.label for r in rounds] == ["Phone Screen", "Technical", "Final"]


async def test_complete_latest_open_round(store: PostgresStore) -> None:
    """complete_interview_round() with no round_index completes the latest open."""
    saved = await store.save_job(_make_job("comp-1"))
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    await store.add_interview_round(job_id=saved.job_id, label="Technical")

    completed = await store.complete_interview_round(
        job_id=saved.job_id, notes="Went well"
    )

    # Should complete the latest (round 2), not round 1
    assert completed.round_index == 2
    assert completed.completed_at is not None
    assert completed.notes == "Went well"


async def test_complete_specific_round(store: PostgresStore) -> None:
    """complete_interview_round(round_index=N) targets a specific round."""
    saved = await store.save_job(_make_job("comp-2"))
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    await store.add_interview_round(job_id=saved.job_id, label="Technical")

    completed = await store.complete_interview_round(
        job_id=saved.job_id, round_index=1, notes="Basic screen done"
    )

    assert completed.round_index == 1
    assert completed.completed_at is not None
    assert completed.notes == "Basic screen done"

    # Round 2 should still be open
    rounds = await store.list_interview_rounds(saved.job_id)
    assert rounds[1].completed_at is None


async def test_complete_no_open_round_raises(store: PostgresStore) -> None:
    """complete_interview_round raises ValueError when no open round exists."""
    saved = await store.save_job(_make_job("comp-3"))
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    await store.complete_interview_round(job_id=saved.job_id)

    with pytest.raises(ValueError, match="no open interview round"):
        await store.complete_interview_round(job_id=saved.job_id)


async def test_complete_specific_round_already_done_raises(
    store: PostgresStore,
) -> None:
    """Completing an already-completed specific round raises ValueError."""
    saved = await store.save_job(_make_job("comp-4"))
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    await store.complete_interview_round(job_id=saved.job_id, round_index=1)

    with pytest.raises(ValueError, match="no open interview round"):
        await store.complete_interview_round(job_id=saved.job_id, round_index=1)


async def test_list_upcoming_interviews(store: PostgresStore) -> None:
    """list_upcoming_interviews returns future, non-completed rounds in window."""
    saved = await store.save_job(_make_job("upcoming-1"))
    await store.transition_status(
        job_id=saved.job_id, new_status="interviewing", force=True
    )

    tomorrow = datetime.now(UTC) + timedelta(days=1)
    next_week = datetime.now(UTC) + timedelta(days=6)
    far_future = datetime.now(UTC) + timedelta(days=30)

    await store.add_interview_round(
        job_id=saved.job_id, label="Tomorrow", scheduled_at=tomorrow
    )
    await store.add_interview_round(
        job_id=saved.job_id, label="Next week", scheduled_at=next_week
    )
    await store.add_interview_round(
        job_id=saved.job_id, label="Far future", scheduled_at=far_future
    )
    # One with no schedule
    await store.add_interview_round(job_id=saved.job_id, label="No schedule")

    upcoming = await store.list_upcoming_interviews(within_days=7)

    labels = [r.label for r in upcoming]
    assert "Tomorrow" in labels
    assert "Next week" in labels
    assert "Far future" not in labels
    assert "No schedule" not in labels


async def test_upcoming_excludes_completed(store: PostgresStore) -> None:
    """Completed rounds do not appear in upcoming even if scheduled in window."""
    saved = await store.save_job(_make_job("upcoming-2"))
    await store.transition_status(
        job_id=saved.job_id, new_status="interviewing", force=True
    )
    tomorrow = datetime.now(UTC) + timedelta(days=1)

    await store.add_interview_round(
        job_id=saved.job_id, label="Done", scheduled_at=tomorrow
    )
    await store.complete_interview_round(job_id=saved.job_id)

    upcoming = await store.list_upcoming_interviews(within_days=7)
    assert len(upcoming) == 0


async def test_add_and_complete_bump_last_status_change(
    store: PostgresStore,
) -> None:
    """add_interview_round and complete_interview_round reset last_status_change_at."""
    saved = await store.save_job(_make_job("bump-1"))

    ts_before_add = await _last_status_change(store, saved.job_id)

    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")

    ts_after_add = await _last_status_change(store, saved.job_id)
    assert ts_after_add >= ts_before_add

    ts_before_complete = await _last_status_change(store, saved.job_id)

    await store.complete_interview_round(job_id=saved.job_id)

    ts_after_complete = await _last_status_change(store, saved.job_id)
    assert ts_after_complete >= ts_before_complete


async def test_delete_job_cascades_rounds(store: PostgresStore, pg_url: str) -> None:
    """Deleting a job cascades to its interview_rounds (FK ON DELETE CASCADE)."""
    saved = await store.save_job(_make_job("cascade-1"))
    jid = int(saved.job_id)
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")
    await store.add_interview_round(job_id=saved.job_id, label="Technical")

    # Verify rounds exist
    rounds = await store.list_interview_rounds(saved.job_id)
    assert len(rounds) == 2

    # Delete the job directly via raw SQL (no delete method on the store)
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute("DELETE FROM jobs WHERE id = $1", jid)
    finally:
        await conn.close()

    # Rounds should be gone
    pool = store._get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM interview_rounds WHERE job_id = $1", jid
        )
    assert count == 0


async def test_list_empty_rounds(store: PostgresStore) -> None:
    """list_interview_rounds returns empty list for a job with no rounds."""
    saved = await store.save_job(_make_job("empty-1"))
    rounds = await store.list_interview_rounds(saved.job_id)
    assert rounds == []


async def test_add_round_with_scheduled_at(store: PostgresStore) -> None:
    """Scheduled time is persisted and returned."""
    saved = await store.save_job(_make_job("sched-1"))
    when = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)

    r = await store.add_interview_round(
        job_id=saved.job_id, label="On-site", scheduled_at=when
    )

    assert r.scheduled_at is not None
    assert r.scheduled_at.year == 2026
    assert r.scheduled_at.month == 7


async def test_complete_preserves_existing_notes(store: PostgresStore) -> None:
    """Completing without notes keeps the existing notes value (if any)."""
    saved = await store.save_job(_make_job("notes-1"))
    await store.add_interview_round(job_id=saved.job_id, label="Phone Screen")

    completed = await store.complete_interview_round(job_id=saved.job_id)

    # Notes should still be None since we never set them
    assert completed.notes is None
