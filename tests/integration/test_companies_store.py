"""Company removal integration tests against PostgreSQL.

Covers mark_company_removed match semantics: only a tracked,
not-already-removed slug matches, so a second removal reports False and a
re-added slug becomes removable again.
"""

from __future__ import annotations

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import CompanyRecord

pytestmark = pytest.mark.postgres


async def test_mark_company_removed_second_call_reports_no_match(
    store: PostgresStore,
) -> None:
    """First removal matches; a repeat on the removed slug returns False."""
    await store.upsert_company(CompanyRecord(slug="double-rm", ats_vendor="lever"))

    assert await store.mark_company_removed("double-rm") is True
    assert await store.mark_company_removed("double-rm") is False


async def test_mark_company_removed_after_readd_matches_again(
    store: PostgresStore,
) -> None:
    """Re-adding a removed slug makes it removable again."""
    await store.upsert_company(CompanyRecord(slug="readd-rm", ats_vendor="lever"))
    assert await store.mark_company_removed("readd-rm") is True

    await store.upsert_company(CompanyRecord(slug="readd-rm", ats_vendor="greenhouse"))
    assert await store.mark_company_removed("readd-rm") is True


async def test_mark_company_removed_unknown_slug_reports_no_match(
    store: PostgresStore,
) -> None:
    """A never-tracked slug returns False."""
    assert await store.mark_company_removed("never-tracked") is False
