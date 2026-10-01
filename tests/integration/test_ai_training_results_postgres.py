"""PostgreSQL Results use the same AI-data role exclusion as SQLite."""

from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.filtering import HardFilters
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


async def test_postgres_results_exclude_contributor_roles(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    expected = []
    try:
        for index, (title, company, blocked) in enumerate(
            [
                ("Software Engineer - AI Trainer", "Example", True),
                ("Software Developer", "DataAnnotation", True),
                ("Full Stack Engineer", "data annotation", True),
                ("LLM Response Evaluator", "Example", True),
                ("Data Labeller", "Example", True),
                ("AI-Training Specialist", "Example", True),
                ("Machine Learning Engineer", "Example", False),
                ("Software Engineer, Data Annotation Platform", "Example", False),
            ]
        ):
            saved = await store.save_job(
                make_job(
                    canonical_id=str(index),
                    title=title,
                    company=company,
                    location="Seattle, WA",
                    discovered_at=datetime.now(UTC),
                )
            )
            if not blocked:
                expected.append(await store.resolve_real_job_id(saved.job_id))
        for filters in (None, HardFilters(location_allowlist=["United States"])):
            page = await store.query_real_jobs_view(
                decision="results", hard_filters=filters
            )
            assert page["total"] == page["tab_counts"]["results"] == len(expected)
            assert {str(row["real_job_id"]) for row in page["jobs"]} == set(expected)
            selected = await store.select_real_job_ids(
                decision="results", hard_filters=filters
            )
            assert set(selected["real_job_ids"]) == set(expected)
    finally:
        await store.close()
