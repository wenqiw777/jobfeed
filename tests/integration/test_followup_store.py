"""Follow-up scheduling integration tests against PostgreSQL.

Covers set_followup round-tripping through list_statuses(needs_followup=True)
and the False return when no job_status row exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import QualityBand, StatusFilter, TransitionRequest
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


def _make_job(canonical_id: str) -> object:
    """Shortcut for a minimal job fixture.

    Args:
        canonical_id: Source-specific natural identity.

    Returns:
        Job posting fixture.
    """
    return make_job(canonical_id, jd_text="JD text", jd_quality=QualityBand.GOOD)


async def _save_applied(store: PostgresStore, canonical_id: str) -> str:
    """Save a job and walk it to 'applied'.

    Args:
        store: Connected store.
        canonical_id: Source-specific natural identity.

    Returns:
        Store-assigned job id.
    """
    saved = await store.save_job(_make_job(canonical_id))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="scored", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )
    return saved.job_id


async def test_set_followup_due_date_round_trips(store: PostgresStore) -> None:
    """A past follow-up date surfaces via list_statuses(needs_followup=True)."""
    job_id = await _save_applied(store, "fu-due-1")

    updated = await store.set_followup(
        job_id=job_id, at=datetime.now(UTC) - timedelta(days=1)
    )
    assert updated is True

    results = await store.list_statuses(StatusFilter(needs_followup=True))
    assert job_id in [r.job_id for r in results]


async def test_set_followup_future_date_not_yet_due(store: PostgresStore) -> None:
    """A future follow-up date does not show up as needing follow-up yet."""
    job_id = await _save_applied(store, "fu-future-1")

    updated = await store.set_followup(
        job_id=job_id, at=datetime.now(UTC) + timedelta(days=3)
    )
    assert updated is True

    results = await store.list_statuses(StatusFilter(needs_followup=True))
    assert job_id not in [r.job_id for r in results]


async def test_set_followup_later_today_is_already_due(
    store: PostgresStore,
) -> None:
    """A follow-up later TODAY counts as due now (date-level comparison).

    Pins the SQL ``next_followup_at::date <= CURRENT_DATE`` truncation: a
    timestamp at today 23:00 UTC is due even when "now" is earlier in the
    day. Midnight-straddle-safe: if the date flips between set and query,
    yesterday 23:00 is due all the more.
    """
    job_id = await _save_applied(store, "fu-today-1")

    today_late_evening = datetime.now(UTC).replace(
        hour=23, minute=0, second=0, microsecond=0
    )
    updated = await store.set_followup(job_id=job_id, at=today_late_evening)
    assert updated is True

    results = await store.list_statuses(StatusFilter(needs_followup=True))
    assert job_id in [r.job_id for r in results]


async def test_set_followup_missing_status_row_returns_false(
    store: PostgresStore,
) -> None:
    """set_followup returns False for an id with no job_status row."""
    updated = await store.set_followup(job_id="999999", at=datetime.now(UTC))
    assert updated is False
