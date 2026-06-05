"""Tests for save_job persist/hydrate of closed_at and enrich_error fields.

Verifies:
- INSERT round-trips both new columns
- Upsert liveness: open→closed sets closed_at
- Upsert liveness: closed→re-closed keeps earliest closed_at (COALESCE semantics)
- Self-heal: re-upsert with jd_text clears closed_at and enrich_error
- Quality monotonic gate is unchanged
- _job_from_record hydrates both columns
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import QualityBand
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

T1 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 6, 2, 10, 0, tzinfo=UTC)  # T2 > T1


async def test_insert_with_closed_at_and_enrich_error_round_trips(
    store: PostgresStore,
) -> None:
    """Inserting a new posting with closed_at and enrich_error persists both."""
    job = make_job("closed-1", closed_at=T1, enrich_error="timeout")
    result = await store.save_job(job)
    assert result.inserted is True

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at == T1
    assert loaded.enrich_error == "timeout"


async def test_upsert_open_to_closed_sets_closed_at(store: PostgresStore) -> None:
    """Re-upsert of open row with a closed posting (jd_text=None) stores closed_at."""
    # First insert: open, no closed_at
    job = make_job("closed-2", jd_text="Full JD text", jd_quality=QualityBand.FULL)
    result = await store.save_job(job)
    assert result.inserted is True

    # Second upsert: closed signal, no jd_text
    closed_job = make_job(
        "closed-2",
        jd_text=None,
        jd_quality=None,
        closed_at=T1,
        enrich_error=None,
    )
    await store.save_job(closed_job)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at == T1


async def test_upsert_closed_keeps_earliest_closed_at(store: PostgresStore) -> None:
    """A second closed confirm with a later timestamp keeps the earliest closed_at."""
    job = make_job("closed-3", jd_text=None, jd_quality=None, closed_at=T1)
    result = await store.save_job(job)

    # Re-confirm closed with a later timestamp
    job2 = make_job("closed-3", jd_text=None, jd_quality=None, closed_at=T2)
    await store.save_job(job2)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at == T1  # earliest wins (COALESCE keeps stored value)


async def test_upsert_with_jd_text_clears_closed_at_and_enrich_error(
    store: PostgresStore,
) -> None:
    """Re-upsert with non-NULL jd_text self-heals closed_at and enrich_error to NULL."""
    # Start with a closed posting
    job = make_job(
        "closed-4",
        jd_text=None,
        jd_quality=None,
        closed_at=T1,
        enrich_error="scrape_failed",
    )
    result = await store.save_job(job)

    # Re-upsert with full JD text → self-heal
    fresh_job = make_job(
        "closed-4",
        jd_text="Full job description text here.",
        jd_quality=QualityBand.FULL,
    )
    await store.save_job(fresh_job)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is None
    assert loaded.enrich_error is None


async def test_quality_monotonic_gate_unchanged(store: PostgresStore) -> None:
    """A lower-quality re-scan does NOT overwrite a stored full-quality jd_text."""
    # Insert with full quality JD
    job = make_job(
        "closed-5", jd_text="Full job description.", jd_quality=QualityBand.FULL
    )
    result = await store.save_job(job)

    # Re-upsert with missing/None quality
    lower = make_job("closed-5", jd_text=None, jd_quality=None)
    await store.save_job(lower)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.jd_text == "Full job description."
    assert loaded.jd_quality == QualityBand.FULL


async def test_job_from_record_hydrates_closed_at_and_enrich_error(
    store: PostgresStore,
) -> None:
    """_job_from_record populates closed_at and enrich_error from the DB row."""
    job = make_job(
        "closed-6",
        jd_text=None,
        jd_quality=None,
        closed_at=T2,
        enrich_error="rate_limited",
    )
    result = await store.save_job(job)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at == T2
    assert loaded.enrich_error == "rate_limited"


async def test_insert_without_closed_at_has_null_columns(store: PostgresStore) -> None:
    """A normal open posting inserts with NULL closed_at and NULL enrich_error."""
    job = make_job("closed-7", jd_text="Some text.")
    result = await store.save_job(job)

    loaded = await store.get_job(result.job_id)
    assert loaded is not None
    assert loaded.closed_at is None
    assert loaded.enrich_error is None
