"""Postgres uses the same explicit duplicate decisions as the Mini store."""

from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting, QualityBand

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("decision", ["merge", "separate"])
async def test_postgres_decision_keeps_history_and_selected_identity(
    fresh_pg_dsn, decision
):
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ids = []
        for i in (1, 2):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=str(i),
                    url=f"https://linkedin.com/jobs/view/{i}/",
                    title="Engineer",
                    company="Example",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text=f"Build service version {i}. " * 25,
                    jd_quality=QualityBand.FULL,
                )
            )
            ids.append(
                (int(await store.resolve_real_job_id(saved.job_id)), int(saved.job_id))
            )
        async with store._get_pool().acquire() as db:
            await db.execute(
                "UPDATE real_jobs SET identity_review_state='identity_conflict'"
            )
            await db.execute(
                "INSERT INTO real_job_review_cases(left_real_job_id,right_real_job_id,"
                "left_job_id,right_job_id,reason) "
                "VALUES($1,$2,$3,$4,'multiple_identity_candidates')",
                ids[0][0],
                ids[1][0],
                ids[0][1],
                ids[1][1],
            )
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 1
        await store.identity_decide(
            str(ids[0][0]),
            str(ids[1][0]),
            decision=decision,
            target_id=str(ids[1][0]) if decision == "merge" else None,
        )
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
        assert (await store.identity_review(status="reviewed", limit=25, offset=0))[
            "total"
        ] == 1
        if decision == "merge":
            assert await store.resolve_real_job_id(str(ids[0][1])) == str(ids[1][0])
    finally:
        await store.close()
