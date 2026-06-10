"""List output integration tests against PostgreSQL.

Covers case-insensitive (ILIKE) notes matching and the company/title/
last_status_change_at fields on StatusInfo rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import QualityBand, StatusFilter
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


def _make_job(canonical_id: str, **overrides: object) -> object:
    """Shortcut for a minimal job fixture.

    Args:
        canonical_id: Source-specific natural identity.
        **overrides: Optional named JobPosting field overrides.

    Returns:
        Job posting fixture.
    """
    return make_job(
        canonical_id, jd_text="JD text", jd_quality=QualityBand.GOOD, **overrides
    )


async def test_notes_contain_matches_case_insensitively(
    store: PostgresStore,
) -> None:
    """notes_contain='ACME' matches a note saved as lowercase 'acme'."""
    saved = await store.save_job(_make_job("notes-ilike-1"))
    await store.append_note(job_id=saved.job_id, text="talked to acme recruiter")

    results = await store.list_statuses(StatusFilter(notes_contain="ACME"))
    assert saved.job_id in [r.job_id for r in results]


async def test_list_statuses_carries_company_title_change_time(
    store: PostgresStore,
) -> None:
    """list_statuses rows carry company, title, and last_status_change_at."""
    saved = await store.save_job(
        _make_job("listout-1", company="Globex", title="Platform Engineer")
    )

    results = await store.list_statuses()
    row = next(r for r in results if r.job_id == saved.job_id)

    assert row.company == "Globex"
    assert row.title == "Platform Engineer"
    assert row.last_status_change_at is not None


async def test_since_cutoff_excludes_rows_changed_before_midnight(
    store: PostgresStore,
) -> None:
    """since=<today midnight UTC> drops a row changed yesterday ~18:00 UTC."""
    yesterday_job = await store.save_job(_make_job("since-yesterday-1"))
    today_job = await store.save_job(_make_job("since-today-1"))

    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_status SET last_status_change_at = $1 WHERE job_id = $2",
            midnight - timedelta(hours=6),  # yesterday ~18:00 UTC
            int(yesterday_job.job_id),
        )
        await conn.execute(
            "UPDATE job_status SET last_status_change_at = $1 WHERE job_id = $2",
            midnight + timedelta(hours=1),  # today 01:00 UTC
            int(today_job.job_id),
        )

    results = await store.list_statuses(StatusFilter(since=midnight))
    job_ids = [r.job_id for r in results]
    assert today_job.job_id in job_ids
    assert yesterday_job.job_id not in job_ids


async def test_since_cutoff_includes_row_changed_exactly_at_cutoff(
    store: PostgresStore,
) -> None:
    """The since boundary is inclusive: last_status_change_at == since matches.

    Pins the SQL comparison as ``>=`` — "--days <date>" means that date's
    midnight, and a status change at exactly midnight belongs to that day.
    """
    saved = await store.save_job(_make_job("since-boundary-1"))

    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    pool = store._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_status SET last_status_change_at = $1 WHERE job_id = $2",
            midnight,
            int(saved.job_id),
        )

    results = await store.list_statuses(StatusFilter(since=midnight))
    assert saved.job_id in [r.job_id for r in results]


async def test_get_status_carries_company_title(store: PostgresStore) -> None:
    """get_status shares the row factory, so it carries company/title too."""
    saved = await store.save_job(
        _make_job("getstatus-1", company="Initech", title="SRE")
    )

    info = await store.get_status(saved.job_id)
    assert info is not None
    assert info.company == "Initech"
    assert info.title == "SRE"
