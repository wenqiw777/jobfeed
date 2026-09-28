"""Canonical priority is one row per real job without a fingerprint."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.services.canonical_priority import canonical_priority_rows


async def test_aliases_share_one_priority_identity(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "priority.db")
    await store.connect()
    try:
        source_ids = []
        for platform, key, url in (
            ("linkedin", "123", "https://www.linkedin.com/jobs/view/123/"),
            ("jobright", "other", "https://careers.southwestair.com/us/en/job/REQ123/engineer"),
        ):
            saved = await store.save_job(JobPosting(
                platform=platform, canonical_id=key, url=url,
                title="Software Engineer", company="Southwest Airlines", location="Dallas, TX",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            ))
            source_ids.append(saved.job_id)
        real_ids = await store.resolve_real_job_ids(source_ids)
        inputs = await store.load_real_job_priority_inputs(real_ids)
        rows = canonical_priority_rows(inputs, now=datetime.now(UTC))
        assert len(rows) == 1
        assert rows[0].real_job_id == real_ids[0]
        assert rows[0].source_job_id == source_ids[1]
        assert rows[0].job.id == source_ids[1]
    finally:
        await store.close()


@pytest.mark.postgres
async def test_postgres_aliases_share_one_priority_identity(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ids = []
        for platform, key, url in (
            ("linkedin", "123", "https://www.linkedin.com/jobs/view/123/"),
            ("jobright", "other", "https://careers.southwestair.com/us/en/job/REQ123/engineer"),
        ):
            saved = await store.save_job(JobPosting(
                platform=platform, canonical_id=key, url=url,
                title="Software Engineer", company="Southwest Airlines", location="Dallas, TX",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            ))
            ids.append(saved.job_id)
        real_ids = await store.resolve_real_job_ids(ids)
        rows = canonical_priority_rows(
            await store.load_real_job_priority_inputs(real_ids), now=datetime.now(UTC)
        )
        assert len(rows) == 1
        assert rows[0].real_job_id == real_ids[0]
        assert rows[0].source_job_id == ids[1]
    finally:
        await store.close()
