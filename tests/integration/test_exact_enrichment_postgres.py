"""Postgres migration and exact-enrichment parity against the real engine."""

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.domain.models import JobPosting, PipelineRun, QualityBand

pytestmark = pytest.mark.postgres


async def test_aggregator_alias_returns_resolved_official_identity(store):
    now = datetime.now(UTC)
    for platform, canonical_id, url, quality in [
        (
            "jobright",
            "abc",
            "https://boards.greenhouse.io/hrt/jobs/8052122",
            QualityBand.PARTIAL,
        ),
        ("speedyapply", "alias", "https://jobright.ai/jobs/info/abc", QualityBand.FULL),
    ]:
        await store.save_job(
            JobPosting(
                platform=platform,
                canonical_id=canonical_id,
                company="HRT",
                title="SWE",
                location="NY",
                discovered_at=now,
                url=url,
                jd_text="Stored description",
                jd_quality=quality,
            )
        )
    match = await store.get_enrichment_by_identity("greenhouse:8052122")
    assert match.quality == QualityBand.FULL
    assert match.external_identity == "greenhouse:8052122"


async def test_identity_retry_and_progress_roundtrip(store):
    now = datetime.now(UTC)
    job = JobPosting(
        platform="speedyapply",
        canonical_id="retry",
        company="HRT",
        title="SWE",
        location="NY",
        discovered_at=now,
        url="https://hrt.com/?gh_jid=8052122",
        jd_quality=QualityBand.MISSING,
        enrich_attempted_at=now,
        enrich_error="identity not found",
        enrich_error_code="identity_not_found",
        enrich_retry_after=now + timedelta(days=7),
    )
    saved = await store.save_job(job)
    loaded = await store.get_job(saved.job_id)
    assert loaded.external_identity == "greenhouse:8052122"
    match = await store.get_enrichment_by_identity("greenhouse:8052122")
    assert match.enrich_retry_after == job.enrich_retry_after
    run = PipelineRun(
        run_id="exact-test",
        source="all",
        started_at=now,
        scan_progress={
            "speedyapply": {"phase": "browser_enrichment", "processed": 2, "total": 5}
        },
    )
    await store.record_pipeline_run(run)
    assert (await store.get_pipeline_run(run.run_id)).scan_progress == run.scan_progress
