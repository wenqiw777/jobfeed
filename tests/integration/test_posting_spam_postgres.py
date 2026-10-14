"""The alternate store uses the same evidence, decisions and SQL exclusions."""

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from tests.integration.test_posting_spam import publisher

pytestmark = pytest.mark.postgres
EXPECTED_POSTINGS = 3


async def test_postgres_spam_roundtrip(fresh_pg_dsn):
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        for i in range(1, 4):
            saved = await store.save_job(publisher(i))
        assert await store.get_state("spam:company:new publisher") == "auto_block"
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
        assert (await store.select_real_job_ids(decision="results"))[
            "real_job_ids"
        ] == []
        parent = await store.resolve_real_job_id(saved.job_id)
        assert await store.claim_real_job_stage_a_by_ids([parent]) == []
        await store.spam_decide(saved.job_id, scope="company", decision="allow")
        assert (await store.query_real_jobs_view(decision="results"))[
            "total"
        ] == EXPECTED_POSTINGS
        assert len(await store.claim_real_job_stage_a_by_ids([parent])) == 1
        assert (await store.spam_review(status="reviewed"))[
            "total"
        ] == EXPECTED_POSTINGS
        await store.spam_decide(saved.job_id, scope="company", decision="reset")
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 0
        assert await store.get_job(saved.job_id)
    finally:
        await store.close()
