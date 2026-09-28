"""Persistence and cross-source lookup of complete and deferred enrichment."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand


async def test_exact_identity_lookup_and_retry_roundtrip(tmp_path):
    store = SQLiteStore(tmp_path / "exact.sqlite")
    await store.connect()
    now = datetime.now(UTC)
    job = JobPosting(
        platform="handshake",
        canonical_id="one",
        company="HRT",
        title="Engineer",
        location="NY",
        discovered_at=now,
        url="https://www.hudsonrivertrading.com/careers/job/?gh_jid=8052122",
        jd_text="Complete JD",
        jd_quality=QualityBand.FULL,
    )
    try:
        saved = await store.save_job(job)
        loaded = await store.get_job(saved.job_id)
        assert loaded.external_identity == "greenhouse:8052122"
        stored = await store.get_enrichment_by_identity("greenhouse:8052122")
        assert stored.jd_text == "Complete JD"
        assert stored.platform == "handshake"
        assert await store.get_enrichment_by_identity("greenhouse:999") is None
        retry = replace(
            job,
            canonical_id="retry",
            jd_text=None,
            jd_quality=QualityBand.MISSING,
            url="https://boards.greenhouse.io/acme/jobs/999",
            enrich_attempted_at=now,
            enrich_error_code="identity_not_found",
            enrich_error="Job identity not found in page",
            enrich_retry_after=now + timedelta(days=7),
        )
        result = await store.save_job(retry)
        await store.close()
        await store.connect()
        loaded = await store.get_job(result.job_id)
        assert loaded.enrich_retry_after == retry.enrich_retry_after
        stored = await store.get_enrichment_by_identity("greenhouse:999")
        assert stored.enrich_error_code == "identity_not_found"
        assert stored.enrich_retry_after == retry.enrich_retry_after
    finally:
        await store.close()


async def test_aggregator_native_id_links_legacy_full_jd_without_title_matching(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "aliases.sqlite")
    await store.connect()
    now = datetime.now(UTC)
    try:
        await store.save_job(
            JobPosting(
                platform="jobright",
                canonical_id="abc",
                company="HRT",
                title="A",
                location="NY",
                discovered_at=now,
                url="https://boards.greenhouse.io/hrt/jobs/8052122",
                jd_quality=QualityBand.PARTIAL,
                jd_text="summary",
            )
        )
        await store.save_job(
            JobPosting(
                platform="speedyapply",
                canonical_id="other",
                company="HRT",
                title="B",
                location="NY",
                discovered_at=now,
                url="https://jobright.ai/jobs/info/abc",
                jd_quality=QualityBand.FULL,
                jd_text="Complete stored description",
            )
        )
        match = await store.get_enrichment_by_identity("greenhouse:8052122")
        assert match.jd_text == "Complete stored description"
        assert match.external_identity == "greenhouse:8052122"
        await store.close()
        await store.connect()
        match = await store.get_enrichment_by_identity("greenhouse:8052122")
        assert match.jd_text == "Complete stored description"
    finally:
        await store.close()


async def test_existing_sqlite_adds_retry_columns_without_losing_jobs(tmp_path):
    """Opening the previous schema preserves jobs and adds nullable retry fields."""

    path = tmp_path / "upgrade.sqlite"
    store = SQLiteStore(path)
    await store.connect()
    saved = await store.save_job(
        JobPosting(
            platform="handshake",
            canonical_id="kept",
            company="Acme",
            title="Engineer",
            location="US",
            url="https://example.test/kept",
            discovered_at=datetime.now(UTC),
        )
    )
    await store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_jobs_external_identity")
        for column in (
            "external_identity",
            "enrich_attempted_at",
            "enrich_error_code",
            "enrich_retry_after",
        ):
            connection.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
    await store.connect()
    try:
        loaded = await store.get_job(saved.job_id)
        assert loaded.canonical_id == "kept"
        assert loaded.enrich_retry_after is None
        with sqlite3.connect(path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        assert {
            "external_identity",
            "enrich_attempted_at",
            "enrich_error_code",
            "enrich_retry_after",
        } <= columns
    finally:
        await store.close()
