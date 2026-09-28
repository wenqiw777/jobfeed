"""Interleaved scan lanes retain independent progress."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from jobfeed.domain.models import PipelineRun
from jobfeed.ports.source import SourceFetchProgress
from jobfeed.services.scan import ScanService


def test_interleaved_updates_preserve_each_source():
    run = PipelineRun(run_id="test", source="all", started_at=datetime.now(UTC))
    service = ScanService(MagicMock(), MagicMock())
    service._on_progress = None
    service._publish_fetch_progress(
        run,
        "speedyapply",
        SourceFetchProgress(processed=2, total=10, phase="browser_enrichment"),
    )
    service._publish_fetch_progress(
        run, "jobright", SourceFetchProgress(processed=20, total=100)
    )
    assert run.scan_progress["speedyapply"]["phase"] == "browser_enrichment"
    assert run.scan_progress["speedyapply"]["processed"] == 2
    assert run.scan_progress["jobright"]["processed"] == 20
