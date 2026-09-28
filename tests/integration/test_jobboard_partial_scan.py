import asyncio
from datetime import UTC, datetime

import pytest
import structlog

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting
from jobfeed.ports.source import PartialSourceFetchError
from jobfeed.services.scan import ScanService


async def test_limited_scan_saves_received_body_and_records_error(tmp_path):
    body = "Full job body including qualifications and benefits. " * 100

    class LimitedSource:
        async def fetch_jobs(self, _config):
            raise PartialSourceFetchError(
                "HTTP 429; Retry-After: 60",
                [
                    JobPosting(
                        platform="handshake",
                        canonical_id="test-job",
                        title="Engineer",
                        company="Acme",
                        location="Remote",
                        url="https://example.test/1",
                        discovered_at=datetime.now(UTC),
                        jd_text=body,
                    )
                ],
            )

    store = SQLiteStore(tmp_path / "partial.db")
    await store.connect()
    try:
        phases = []
        with pytest.raises(RuntimeError, match="failed source work"):
            await ScanService(store, structlog.get_logger("test")).run(
                [("handshake", LimitedSource(), {})],
                on_progress=lambda run: phases.append(
                    run.scan_progress.get("handshake", {}).get("phase")
                ),
            )
        run = (await store.list_pipeline_runs())[0][0]
        assert run.status == "failed"
        assert run.failed_source == "handshake"
        jobs = await store.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].jd_text == body
        assert run.jobs_inserted == 1
        assert run.errors
        assert run.scan_progress["handshake"]["total"] is None
        assert "completed" not in phases
    finally:
        await store.close()


async def test_partial_failure_waits_for_other_sources_before_finalizing(tmp_path):
    """A failed source cannot release the run lease while another source saves."""

    def posting(identity):
        return JobPosting(
            platform="handshake",
            canonical_id=identity,
            title="Engineer",
            company="Acme",
            location="Remote",
            url=f"https://example.test/{identity}",
            discovered_at=datetime.now(UTC),
            jd_text="Full job description",
        )

    class Partial:
        async def fetch_jobs(self, _config):
            raise PartialSourceFetchError("HTTP 429", [posting("partial")])

    class Delayed:
        async def fetch_jobs(self, _config):
            await asyncio.sleep(0.02)
            return [posting("complete")]

    store = SQLiteStore(tmp_path / "parallel-partial.db")
    await store.connect()
    try:
        with pytest.raises(RuntimeError, match="failed source work"):
            await ScanService(store, structlog.get_logger("test")).run(
                [("partial", Partial(), {}), ("delayed", Delayed(), {})]
            )
        run = (await store.list_pipeline_runs())[0][0]
        assert run.status == "failed"
        assert run.failed_source == "partial"
        expected_ids = {"partial", "complete"}
        assert run.jobs_inserted == len(expected_ids)
        assert {job.canonical_id for job in await store.list_jobs()} == {
            "partial",
            "complete",
        }
    finally:
        # Let a broken implementation's unawaited source finish before closing.
        await asyncio.sleep(0.05)
        await store.close()
