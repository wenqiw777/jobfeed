"""Real PostgreSQL migration, upsert, claim and pagination parity."""

from dataclasses import replace

import pytest

from jobfeed.domain.models_views import JobsViewQuery
from tests.support.sqlite_jobs_evaluations import make_job, stage_a

pytestmark = pytest.mark.postgres


async def test_repost_upsert_claims_and_sorting(store):
    repost = make_job("old")
    saved = await store.save_job(repost)
    await store.save_stage_a(saved.job_id, stage_a(99))
    await store.save_job(
        replace(
            repost,
            is_repost=True,
            repost_evidence="Reposted 1 day ago",
            repost_observed_at=repost.discovered_at,
        )
    )
    await store.save_job(repost)
    loaded = await store.get_job(saved.job_id)
    assert loaded.is_repost is True
    assert loaded.discovered_at == repost.discovered_at
    assert (await store.get_evaluation(saved.job_id)).stage_a.score == stage_a(99).score
    assert await store.claim_stage_a_by_ids([saved.job_id], corpus="all") == []
    assert await store.claim_pending_stage_b() == []
    normal = await store.save_job(make_job("new"))
    await store.save_stage_a(normal.job_id, stage_a(91))
    page = await store.query_jobs_view(
        JobsViewQuery(tab="all", sort="triage_score_desc", limit=1)
    )
    assert page.rows[0].job.id == normal.job_id
    page = await store.query_jobs_view(
        JobsViewQuery(tab="all", sort="triage_score_desc", limit=1, offset=1)
    )
    assert page.rows[0].job.is_repost is True
