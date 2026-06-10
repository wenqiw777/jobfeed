"""Twin cluster expansion and bulk cascade integration tests against PostgreSQL.

Covers expand_twin_ids grouping by normalized company/title, blank-norm
singleton guard, transition_status_bulk cascade with correct reason tags,
and per-cluster failure isolation.
"""

from __future__ import annotations

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import QualityBand
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


def _make_job(
    canonical_id: str,
    *,
    company: str = "Example",
    title: str = "Backend Intern",
) -> object:
    """Shortcut for a minimal job fixture with controllable company/title.

    Args:
        canonical_id: Source-specific natural identity.
        company: Company name.
        title: Job title.

    Returns:
        Job posting fixture.
    """
    return make_job(
        canonical_id,
        jd_text="JD text",
        jd_quality=QualityBand.GOOD,
        company=company,
        title=title,
    )


async def test_expand_twin_ids_groups_by_norms(store: PostgresStore) -> None:
    """Jobs with the same company+title norm form a twin cluster."""
    s1 = await store.save_job(_make_job("twin-a1", company="Acme Corp", title="SWE"))
    s2 = await store.save_job(_make_job("twin-a2", company="Acme Corp", title="SWE"))
    s3 = await store.save_job(_make_job("twin-b1", company="Other Inc", title="SWE"))

    result = await store.expand_twin_ids([int(s1.job_id), int(s3.job_id)])

    # s1 and s2 share norms, so s1 expands to both
    cluster_a = result[int(s1.job_id)]
    assert int(s1.job_id) in cluster_a
    assert int(s2.job_id) in cluster_a
    # s3 is a different company
    cluster_b = result[int(s3.job_id)]
    assert int(s3.job_id) in cluster_b
    assert int(s1.job_id) not in cluster_b


async def test_expand_twin_ids_blank_norm_singleton(
    store: PostgresStore,
) -> None:
    """A job with blank company expands only to itself."""
    s1 = await store.save_job(_make_job("blank-1", company="", title="SWE"))

    result = await store.expand_twin_ids([int(s1.job_id)])

    assert result[int(s1.job_id)] == [int(s1.job_id)]


async def test_expand_twin_ids_missing_job(store: PostgresStore) -> None:
    """A nonexistent job_id expands to itself as a singleton."""
    result = await store.expand_twin_ids([999999])
    assert result[999999] == [999999]


async def test_expand_twin_ids_empty_input(store: PostgresStore) -> None:
    """Empty input returns empty dict."""
    result = await store.expand_twin_ids([])
    assert result == {}


async def test_bulk_transition_cascades_to_twins(store: PostgresStore) -> None:
    """transition_status_bulk cascades status to twin cluster members."""
    s1 = await store.save_job(_make_job("bulk-c1", company="BulkCo", title="Dev"))
    s2 = await store.save_job(_make_job("bulk-c2", company="BulkCo", title="Dev"))

    # Move both to applied state
    for s in [s1, s2]:
        await store.transition_status(
            job_id=s.job_id,
            new_status="scored",
            force=True,
        )
        await store.transition_status(
            job_id=s.job_id,
            new_status="applied",
            force=True,
        )

    result = await store.transition_status_bulk(
        [(s1.job_id, "interviewing")],
        reason_selected="user-action",
        reason_cascade="twin-cascade",
    )

    assert result.succeeded == 2  # s1 + s2
    assert result.failed == []

    # Both should now be interviewing
    info1 = await store.get_status(s1.job_id)
    info2 = await store.get_status(s2.job_id)
    assert info1 is not None and info1.status == "interviewing"
    assert info2 is not None and info2.status == "interviewing"


async def test_bulk_transition_skips_terminal_twins(
    store: PostgresStore,
) -> None:
    """Terminal twin-cluster siblings are skipped, not transitioned."""
    s1 = await store.save_job(_make_job("bulk-t1", company="TermCo", title="PM"))
    s2 = await store.save_job(_make_job("bulk-t2", company="TermCo", title="PM"))

    # s1 -> applied, s2 -> rejected (terminal)
    for s in [s1, s2]:
        await store.transition_status(
            job_id=s.job_id,
            new_status="scored",
            force=True,
        )
    await store.transition_status(
        job_id=s1.job_id,
        new_status="applied",
        force=True,
    )
    await store.transition_status(
        job_id=s2.job_id,
        new_status="rejected",
        force=True,
    )

    result = await store.transition_status_bulk(
        [(s1.job_id, "interviewing")],
        reason_selected="user-action",
        reason_cascade="twin-cascade",
    )

    assert result.succeeded == 1  # only s1
    assert result.skipped == 1  # s2 is terminal

    info2 = await store.get_status(s2.job_id)
    assert info2 is not None and info2.status == "rejected"


async def test_bulk_transition_records_failure(store: PostgresStore) -> None:
    """A cluster failure is recorded; other clusters proceed."""
    s_ok = await store.save_job(_make_job("bulk-ok1", company="OkCo", title="Dev"))
    await store.transition_status(
        job_id=s_ok.job_id,
        new_status="scored",
        force=True,
    )
    await store.transition_status(
        job_id=s_ok.job_id,
        new_status="applied",
        force=True,
    )

    # Use a nonexistent job_id to trigger a KeyError in _transition_status_in_tx
    bad_id = "999999"

    result = await store.transition_status_bulk(
        [(bad_id, "interviewing"), (s_ok.job_id, "interviewing")],
        reason_selected="user-action",
        reason_cascade="twin-cascade",
    )

    assert result.succeeded >= 1
    assert len(result.failed) == 1
    assert result.failed[0][0] == bad_id


async def test_bulk_transition_reason_tags(store: PostgresStore) -> None:
    """Selected job gets reason_selected; twins get reason_cascade."""
    s1 = await store.save_job(_make_job("reason-1", company="ReasonCo", title="Eng"))
    s2 = await store.save_job(_make_job("reason-2", company="ReasonCo", title="Eng"))

    for s in [s1, s2]:
        await store.transition_status(
            job_id=s.job_id,
            new_status="scored",
            force=True,
        )
        await store.transition_status(
            job_id=s.job_id,
            new_status="applied",
            force=True,
        )

    await store.transition_status_bulk(
        [(s1.job_id, "interviewing")],
        reason_selected="my-reason",
        reason_cascade="cascade-reason",
    )

    # Check history for reason tags
    pool = store._get_pool()
    async with pool.acquire() as conn:
        h1 = await conn.fetchrow(
            "SELECT reason FROM job_status_history"
            " WHERE job_id = $1 AND to_status = 'interviewing'"
            " ORDER BY changed_at DESC LIMIT 1",
            int(s1.job_id),
        )
        h2 = await conn.fetchrow(
            "SELECT reason FROM job_status_history"
            " WHERE job_id = $1 AND to_status = 'interviewing'"
            " ORDER BY changed_at DESC LIMIT 1",
            int(s2.job_id),
        )
    assert h1 is not None and h1["reason"] == "my-reason"
    assert h2 is not None and h2["reason"] == "cascade-reason"
