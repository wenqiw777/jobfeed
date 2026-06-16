"""Unit tests for ScanService SessionSource orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from jobfeed.domain.errors import SourceBusyError
from jobfeed.domain.models import JobPosting, QualityBand, SaveJobResult
from jobfeed.ports.source import DiscoverResult, EnrichResult
from jobfeed.services.scan import ScanService

GOOD_JD = (
    "Build reliable job-search infrastructure with async Python, clean service "
    "boundaries, deterministic tests, operational logging, and careful source "
    "adapters that keep external scraping details out of orchestration code."
)
ERROR_JD = "Fallback card text"
POSTED_AT = datetime(2026, 5, 30, tzinfo=UTC)


class RecordingStore:
    """Minimal store double for the scan path."""

    def __init__(self) -> None:
        self.jobs: list[JobPosting] = []
        self.runs: list[object] = []

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Record a saved job and report an insert."""
        self.jobs.append(job)
        return SaveJobResult(job_id=job.canonical_id, inserted=True, updated=False)

    async def record_pipeline_run(self, run: object) -> None:
        """Record the final pipeline run."""
        self.runs.append(run)

    async def record_step_timing(self, _timing: object) -> None:
        """Accept and discard step timings (scan path doesn't assert on them)."""


class RecordingLogger:
    """Small in-memory logger for service assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> object:
        """Record an info event."""
        self.events.append((event, kwargs))
        return kwargs

    def error(self, event: str, **kwargs: object) -> object:
        """Record an error event."""
        self.events.append((event, kwargs))
        return kwargs


class StaticSimpleSource:
    """SimpleSource double returning configured postings."""

    def __init__(self, jobs: list[JobPosting]) -> None:
        self.jobs = jobs

    async def fetch_jobs(self, _config: dict[str, object]) -> list[JobPosting]:
        """Return the configured jobs."""
        return self.jobs


class FakeScanSession:
    """ScanSession double recording discover/enrich calls in order."""

    def __init__(
        self,
        discovered: DiscoverResult,
        results: dict[str, EnrichResult],
        opened_flag: list[bool],
    ) -> None:
        self.discovered = discovered
        self.results = results
        self._opened_flag = opened_flag
        self.discover_config: dict[str, object] | None = None
        self.enrich_calls: list[str] = []

    async def discover(self, config: dict[str, object]) -> DiscoverResult:
        """Return the configured discovery, asserting the session is open."""
        assert self._opened_flag[0] is True  # lock/session must wrap discover
        self.discover_config = config
        return self.discovered

    async def enrich(self, posting: JobPosting) -> EnrichResult:
        """Return the configured enrichment for a posting."""
        self.enrich_calls.append(posting.canonical_id)
        return self.results[posting.canonical_id]


class FakeSessionSource:
    """SessionSource double with injectable session-entry failures."""

    def __init__(
        self,
        discovered: DiscoverResult,
        results: dict[str, EnrichResult] | None = None,
        session_error: Exception | None = None,
    ) -> None:
        self.discovered = discovered
        self.results = results or {}
        self.session_error = session_error
        self.session_opened = False
        self.session_closed = False
        self._opened_flag = [False]
        self.scan_session = FakeScanSession(discovered, self.results, self._opened_flag)

    def session(self) -> object:
        """Open a fake async scan session (lock + browser stand-in)."""
        outer = self

        @asynccontextmanager
        async def manager() -> AsyncIterator[FakeScanSession]:
            if outer.session_error is not None:
                raise outer.session_error
            outer.session_opened = True
            outer._opened_flag[0] = True
            try:
                yield outer.scan_session
            finally:
                outer.session_closed = True
                outer._opened_flag[0] = False

        return manager()


@pytest.mark.asyncio
async def test_scan_service_enriches_session_source_postings() -> None:
    """SessionSource postings are discovered+enriched, saved, counted."""
    posting = _posting("li-1")
    source = FakeSessionSource(
        DiscoverResult(postings=[posting]),
        {
            "li-1": EnrichResult(
                jd_text=GOOD_JD,
                quality=QualityBand.GOOD,
                enrich_source="linkedin_search_pane",
                posted_at=POSTED_AT,
            )
        },
    )
    store = RecordingStore()

    run = await ScanService(store, RecordingLogger()).run(
        [("linkedin", source, {"max_jobs": 1})]
    )

    saved = store.jobs[0]
    assert source.scan_session.discover_config == {"max_jobs": 1}
    assert source.scan_session.enrich_calls == ["li-1"]
    assert source.session_opened is True
    assert source.session_closed is True
    assert saved.jd_text == GOOD_JD
    assert saved.enrich_source == "linkedin_search_pane"
    assert saved.posted_at == POSTED_AT
    assert saved.enriched_at is not None
    assert run.jobs_inserted == 1
    assert run.errors == 0


@pytest.mark.asyncio
async def test_session_source_needs_reauth_counts_as_source_error() -> None:
    """A reauth discovery result is recoverable and saves no postings."""
    source = FakeSessionSource(
        DiscoverResult(
            postings=[_posting("li-auth")],
            needs_reauth=True,
            error="LinkedIn login required",
        )
    )
    store = RecordingStore()
    logger = RecordingLogger()

    run = await ScanService(store, logger).run([("linkedin", source, {})])

    assert store.jobs == []
    assert source.session_closed is True  # session opened to run discover, then closed
    assert run.errors == 1
    assert (
        "scan_source_failed",
        {"source": "linkedin", "error": "LinkedIn login required"},
    ) in logger.events


@pytest.mark.asyncio
async def test_session_contention_is_skipped_not_errored() -> None:
    """A busy session (lock held) is a benign skip, not a fetch error."""
    session_source = FakeSessionSource(
        DiscoverResult(postings=[_posting("li-lock")]),
        session_error=SourceBusyError("LinkedIn enrich already running"),
    )
    simple_source = StaticSimpleSource([_posting("mock-1", platform="mock")])
    store = RecordingStore()
    logger = RecordingLogger()

    run = await ScanService(store, logger).run(
        [
            ("linkedin", session_source, {}),
            ("mock", simple_source, {}),
        ]
    )

    assert [job.canonical_id for job in store.jobs] == ["mock-1"]
    assert session_source.session_opened is False
    assert run.jobs_inserted == 1
    assert run.errors == 0  # contention does NOT count as an error
    assert any(event == "scan_source_busy" for event, _ in logger.events)


@pytest.mark.asyncio
async def test_session_generic_failure_preserves_sibling_source() -> None:
    """A non-contention session failure is an error but spares sibling sources."""
    session_source = FakeSessionSource(
        DiscoverResult(postings=[_posting("li-x")]),
        session_error=RuntimeError("browser crashed"),
    )
    simple_source = StaticSimpleSource([_posting("mock-1", platform="mock")])
    store = RecordingStore()

    run = await ScanService(store, RecordingLogger()).run(
        [
            ("linkedin", session_source, {}),
            ("mock", simple_source, {}),
        ]
    )

    assert [job.canonical_id for job in store.jobs] == ["mock-1"]
    assert run.errors == 1


@pytest.mark.asyncio
async def test_posting_enrich_error_is_logged_but_not_run_error() -> None:
    """Per-posting enrich errors save the result and do not fail the source."""
    source = FakeSessionSource(
        DiscoverResult(postings=[_posting("li-card")]),
        {
            "li-card": EnrichResult(
                jd_text=ERROR_JD,
                quality=QualityBand.STUB,
                enrich_source="error",
                error="right pane never loaded",
            )
        },
    )
    store = RecordingStore()
    logger = RecordingLogger()

    run = await ScanService(store, logger).run([("linkedin", source, {})])

    saved = store.jobs[0]
    assert saved.jd_text == ERROR_JD
    assert saved.enrich_source == "error"
    assert saved.enriched_at is None
    assert run.errors == 0
    assert (
        "scan_posting_enrich_failed",
        {
            "source": "linkedin",
            "canonical_id": "li-card",
            "error": "right pane never loaded",
        },
    ) in logger.events


def _posting(canonical_id: str, platform: str = "linkedin") -> JobPosting:
    return JobPosting(
        platform=platform,
        canonical_id=canonical_id,
        url=f"https://example.test/jobs/{canonical_id}",
        title="Backend Intern",
        company="Northstar Systems",
        location="Remote",
        discovered_at=datetime.now(UTC),
        jd_text=None,
        jd_quality=QualityBand.MISSING,
    )
