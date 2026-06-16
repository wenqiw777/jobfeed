"""Status query integration tests against PostgreSQL.

Covers list_statuses no_response_days filter for both applied AND interviewing,
and get_status_history returning newest-first.
"""

from __future__ import annotations

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


async def test_no_response_days_covers_applied(store: PostgresStore) -> None:
    """no_response_days filter returns jobs in 'applied' status."""
    saved = await store.save_job(_make_job("nrd-applied-1"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="scored", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )

    # Backdate last_status_change_at to 10 days ago
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_status SET last_status_change_at = now() - '10 days'::interval"
            " WHERE job_id = $1",
            int(saved.job_id),
        )

    results = await store.list_statuses(StatusFilter(no_response_days=5))
    job_ids = [r.job_id for r in results]
    assert saved.job_id in job_ids


async def test_no_response_days_covers_interviewing(store: PostgresStore) -> None:
    """no_response_days filter returns jobs in 'interviewing' status."""
    saved = await store.save_job(_make_job("nrd-interviewing-1"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="scored", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="interviewing")
    )

    # Backdate last_status_change_at to 10 days ago
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_status SET last_status_change_at = now() - '10 days'::interval"
            " WHERE job_id = $1",
            int(saved.job_id),
        )

    results = await store.list_statuses(StatusFilter(no_response_days=5))
    job_ids = [r.job_id for r in results]
    assert saved.job_id in job_ids


async def test_no_response_days_excludes_recent(store: PostgresStore) -> None:
    """Jobs changed within the grace period are excluded."""
    saved = await store.save_job(_make_job("nrd-recent-1"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="scored", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )
    # Default last_status_change_at is now(), which is within 5 days
    results = await store.list_statuses(StatusFilter(no_response_days=5))
    job_ids = [r.job_id for r in results]
    assert saved.job_id not in job_ids


async def test_get_status_history_newest_first(store: PostgresStore) -> None:
    """get_status_history returns statuses in reverse chronological order."""
    saved = await store.save_job(_make_job("hist-1"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="scored", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="interviewing")
    )

    history = await store.get_status_history(saved.job_id)
    # Trigger seeds an initial "new" history row on job insert
    assert history == ["interviewing", "applied", "scored", "new"]


async def test_get_status_history_fresh_job(store: PostgresStore) -> None:
    """get_status_history for a fresh job returns the trigger-seeded 'new' row."""
    saved = await store.save_job(_make_job("hist-empty-1"))
    history = await store.get_status_history(saved.job_id)
    assert history == ["new"]


async def test_get_status_history_ignores_clock_skew(store: PostgresStore) -> None:
    """History order follows insertion id, not a non-monotonic wall clock.

    changed_at defaults to now() (wall clock at transaction start), which can
    invert under NTP steps or load. Backdate the newest row's changed_at behind
    an older one and confirm get_status_history still returns it first.
    """
    saved = await store.save_job(_make_job("hist-skew-1"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="scored", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )

    # Simulate a backward clock step: the latest row (applied) gets a changed_at
    # one hour BEFORE the prior (scored) row.
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE job_status_history
               SET changed_at = (
                   SELECT changed_at - interval '1 hour'
                   FROM job_status_history
                   WHERE job_id = $1 AND to_status = 'scored'
               )
               WHERE job_id = $1 AND to_status = 'applied'""",
            int(saved.job_id),
        )

    history = await store.get_status_history(saved.job_id)
    assert history == ["applied", "scored", "new"]
