"""PostgreSQL follows the same destination and paged-content identity rules."""

from dataclasses import replace

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from tests.support.sqlite_jobs_evaluations import make_job

pytestmark = pytest.mark.postgres
BODY = "Build distributed services and maintain production Python applications. " * 12


async def test_destination_and_large_content_group(fresh_pg_dsn):
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        for n in range(55):
            await store.save_job(make_job(f"unrelated-{n}", jd_text=f"Other role {n}"))
        first = await store.save_job(make_job("matching-one", jd_text=BODY))
        second = await store.save_job(make_job("matching-two", jd_text=BODY))
        assert await store.resolve_real_job_id(
            first.job_id
        ) == await store.resolve_real_job_id(second.job_id)
        parents = []
        for n in range(2):
            saved = await store.save_job(
                replace(
                    make_job(f"destination-{n}", jd_text=BODY),
                    platform="linkedin",
                    url=f"https://linkedin.com/jobs/view/{100 + n}",
                    location=f"City {n}",
                    apply_url="https://epic.avature.net/Careers/RegisterMethod?folderId=30063",
                )
            )
            parents.append(await store.resolve_real_job_id(saved.job_id))
        assert parents[0] == parents[1]
    finally:
        await store.close()
