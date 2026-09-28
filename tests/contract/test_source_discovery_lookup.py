from datetime import UTC, datetime

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand


async def test_bulk_source_id_lookup_preserves_posting_and_platform_scope(tmp_path):
    store = SQLiteStore(tmp_path / "lookup.sqlite")
    await store.connect()
    try:
        for platform in ["linkedin", "handshake"]:
            await store.save_job(
                JobPosting(
                    platform=platform,
                    canonical_id="same-id",
                    title="SWE",
                    company="ACME",
                    location="US",
                    url=f"https://example.com/{platform}",
                    discovered_at=datetime.now(UTC),
                    jd_text=f"Full {platform} JD",
                    jd_quality=QualityBand.FULL,
                )
            )
        jobs = await store.get_jobs_by_canonical_ids(
            platform="linkedin", canonical_ids=["same-id", "same-id", "missing"]
        )
        assert list(jobs) == ["same-id"]
        assert jobs["same-id"].jd_text == "Full linkedin JD"
        assert jobs["same-id"].company == "ACME"
        assert (
            await store.get_jobs_by_canonical_ids(platform="linkedin", canonical_ids=[])
            == {}
        )
    finally:
        await store.close()
