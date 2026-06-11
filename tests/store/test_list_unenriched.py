"""Tests for list_unenriched_jobs, mark_job_closed, and posted_at backfill.

Verifies:
- list_unenriched_jobs returns only rows with jd_text IS NULL and
  closed_at IS NULL for the given platform; enriched, closed, and
  other-platform rows are excluded
- Ordering is newest discovered_at first; limit caps the result
- Returns [] when nothing matches
- mark_job_closed sets closed_at on one row, removing it from the
  unenriched listing; an optional reason is stamped into enrich_error
  for ops triage, and omitting it leaves enrich_error untouched
- record_enrichment(posted_at=X) fills a NULL posted_at but never
  overwrites an existing one; omitting the kwarg leaves the column alone
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting
from tests.support.factories import ENRICHED_TIME, FIXED_TIME, make_job

pytestmark = pytest.mark.postgres

PLATFORM = "linkedin_guest"
CARD_POSTED_AT = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
JD_POSTED_AT = datetime(2026, 6, 3, 18, 30, tzinfo=UTC)
CLOSED_AT = datetime(2026, 6, 9, 8, 0, tzinfo=UTC)


def _discovered(minutes: int) -> datetime:
    """Return a deterministic discovered_at offset by N minutes."""
    return FIXED_TIME + timedelta(minutes=minutes)


def _guest_job(
    canonical_id: str, *, minutes: int = 0, **overrides: object
) -> JobPosting:
    """Build an unenriched linkedin_guest posting fixture."""
    overrides.setdefault("platform", PLATFORM)
    overrides.setdefault("jd_text", None)
    overrides.setdefault("discovered_at", _discovered(minutes))
    return make_job(canonical_id, **overrides)


async def _enrich(
    store: PostgresStore,
    job_id: str,
    *,
    posted_at: datetime | None = None,
    with_kwarg: bool = True,
) -> None:
    """Run record_enrichment with or without the posted_at kwarg."""
    kwargs: dict[str, object] = {}
    if with_kwarg:
        kwargs["posted_at"] = posted_at
    await store.record_enrichment(
        job_id=job_id,
        jd_text="A full job description body for the role.",
        jd_quality="full",
        enriched_at=ENRICHED_TIME,
        enrich_source="linkedin_guest",
        **kwargs,
    )


async def test_returns_only_unenriched_open_rows_for_platform(
    store: PostgresStore,
) -> None:
    """Only platform rows with NULL jd_text and NULL closed_at are returned."""
    posting = _guest_job("lg-open", minutes=0)
    wanted = await store.save_job(posting)
    await store.save_job(
        _guest_job("lg-enriched", minutes=1, jd_text="Already has a JD body.")
    )
    await store.save_job(_guest_job("lg-closed", minutes=2, closed_at=CLOSED_AT))
    await store.save_job(make_job("other-open", platform="other", jd_text=None))

    rows = await store.list_unenriched_jobs(platform=PLATFORM, limit=10)

    assert [(r.job_id, r.canonical_id) for r in rows] == [(wanted.job_id, "lg-open")]
    assert rows[0].url == posting.url


async def test_orders_newest_discovered_first_and_respects_limit(
    store: PostgresStore,
) -> None:
    """Rows come back newest discovered_at first, capped at limit."""
    await store.save_job(_guest_job("lg-oldest", minutes=0))
    middle = await store.save_job(_guest_job("lg-middle", minutes=1))
    newest = await store.save_job(_guest_job("lg-newest", minutes=2))

    rows = await store.list_unenriched_jobs(platform=PLATFORM, limit=2)

    assert [r.job_id for r in rows] == [newest.job_id, middle.job_id]


async def test_returns_empty_list_when_none_match(store: PostgresStore) -> None:
    """No matching rows yields an empty list."""
    await store.save_job(make_job("other-only", platform="other", jd_text=None))

    rows = await store.list_unenriched_jobs(platform=PLATFORM, limit=10)

    assert rows == []


async def test_mark_job_closed_sets_closed_at_and_excludes_row(
    store: PostgresStore,
) -> None:
    """mark_job_closed stamps closed_at; the row leaves the unenriched list."""
    saved = await store.save_job(_guest_job("lg-gone"))

    await store.mark_job_closed(job_id=saved.job_id, closed_at=CLOSED_AT)

    loaded = await store.get_job(saved.job_id)
    assert loaded is not None
    assert loaded.closed_at == CLOSED_AT
    rows = await store.list_unenriched_jobs(platform=PLATFORM, limit=10)
    assert rows == []


async def test_list_unenriched_limit_zero_returns_empty(
    store: PostgresStore,
) -> None:
    """limit=0 returns an empty list even when rows would match."""
    await store.save_job(_guest_job("lg-capped"))

    rows = await store.list_unenriched_jobs(platform=PLATFORM, limit=0)

    assert rows == []


async def test_mark_job_closed_with_reason_stamps_enrich_error(
    store: PostgresStore,
) -> None:
    """A close reason is recorded in enrich_error for ops triage."""
    saved = await store.save_job(_guest_job("lg-reason"))

    await store.mark_job_closed(
        job_id=saved.job_id, closed_at=CLOSED_AT, reason="gone:404:linkedin_guest"
    )

    loaded = await store.get_job(saved.job_id)
    assert loaded is not None
    assert loaded.closed_at == CLOSED_AT
    assert loaded.enrich_error == "gone:404:linkedin_guest"


async def test_mark_job_closed_without_reason_keeps_enrich_error(
    store: PostgresStore,
) -> None:
    """Omitting the reason leaves an existing enrich_error untouched."""
    saved = await store.save_job(
        _guest_job("lg-no-reason", enrich_error="fetch:timeout")
    )

    await store.mark_job_closed(job_id=saved.job_id, closed_at=CLOSED_AT)

    loaded = await store.get_job(saved.job_id)
    assert loaded is not None
    assert loaded.closed_at == CLOSED_AT
    assert loaded.enrich_error == "fetch:timeout"


async def test_mark_job_closed_nonexistent_id_is_noop(
    store: PostgresStore,
) -> None:
    """Closing an id that does not exist is a silent no-op."""
    await store.mark_job_closed(job_id="999999999", closed_at=CLOSED_AT)


async def test_record_enrichment_posted_at_fills_null(store: PostgresStore) -> None:
    """posted_at=X fills a NULL posted_at column."""
    saved = await store.save_job(_guest_job("lg-fill", posted_at=None))

    await _enrich(store, saved.job_id, posted_at=JD_POSTED_AT)

    loaded = await store.get_job(saved.job_id)
    assert loaded is not None
    assert loaded.posted_at == JD_POSTED_AT


async def test_record_enrichment_posted_at_never_overwrites(
    store: PostgresStore,
) -> None:
    """An existing (card-derived, exact) posted_at wins over the JD-page date."""
    saved = await store.save_job(_guest_job("lg-keep", posted_at=CARD_POSTED_AT))

    await _enrich(store, saved.job_id, posted_at=JD_POSTED_AT)

    loaded = await store.get_job(saved.job_id)
    assert loaded is not None
    assert loaded.posted_at == CARD_POSTED_AT


async def test_record_enrichment_posted_at_none_leaves_column(
    store: PostgresStore,
) -> None:
    """posted_at=None leaves both NULL and non-NULL columns untouched."""
    kept = await store.save_job(_guest_job("lg-none-kept", posted_at=CARD_POSTED_AT))
    empty = await store.save_job(_guest_job("lg-none-null", minutes=1))

    await _enrich(store, kept.job_id, posted_at=None)
    await _enrich(store, empty.job_id, posted_at=None)

    loaded_kept = await store.get_job(kept.job_id)
    loaded_empty = await store.get_job(empty.job_id)
    assert loaded_kept is not None and loaded_kept.posted_at == CARD_POSTED_AT
    assert loaded_empty is not None and loaded_empty.posted_at is None


async def test_record_enrichment_without_kwarg_is_unchanged(
    store: PostgresStore,
) -> None:
    """Existing callers (no posted_at kwarg) keep the column untouched."""
    saved = await store.save_job(_guest_job("lg-legacy", posted_at=CARD_POSTED_AT))

    await _enrich(store, saved.job_id, with_kwarg=False)

    loaded = await store.get_job(saved.job_id)
    assert loaded is not None
    assert loaded.posted_at == CARD_POSTED_AT
