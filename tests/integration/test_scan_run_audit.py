"""Per-run scan audit statistics survive later natural-key upserts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.services.scan import ScanService


class _StaticSource:
    def __init__(self, posting: JobPosting) -> None:
        self._posting = posting

    async def fetch_jobs(self, _config: dict[str, object]) -> list[JobPosting]:
        return [self._posting]


async def test_later_scan_upsert_does_not_replace_earlier_incoming_quality(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "jobfeed.db")
    await store.connect()
    service = ScanService(store, structlog.get_logger("test.scan.audit"))

    first = await service.run([("fixture", _StaticSource(_posting(full=True)), {})])
    second = await service.run([("fixture", _StaticSource(_posting(full=False)), {})])

    stored_first = await store.get_pipeline_run(first.run_id)
    stored_second = await store.get_pipeline_run(second.run_id)
    stored_job = await store.get_job("1")

    assert stored_first is not None
    assert stored_first.scan_stats["fixture"] == {
        "fetched": 1,
        "discovered": 1,
        "inserted": 1,
        "updated": 0,
        "has_jd": 1,
        "full": 1,
    }
    assert stored_second is not None
    assert stored_second.scan_stats["fixture"] == {
        "fetched": 1,
        "discovered": 1,
        "inserted": 0,
        "updated": 1,
        "has_jd": 0,
        "missing": 1,
    }
    assert stored_job is not None
    assert stored_job.jd_quality is QualityBand.FULL
    await store.close()


def _posting(*, full: bool) -> JobPosting:
    return JobPosting(
        platform="fixture",
        canonical_id="same-job",
        url="https://example.test/jobs/same-job",
        title="Software Engineer",
        company="Acme",
        location="Remote",
        discovered_at=datetime.now(UTC),
        jd_text="x" * 1200 if full else None,
        jd_quality=QualityBand.FULL if full else QualityBand.MISSING,
    )
