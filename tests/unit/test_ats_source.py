"""Unit tests for the ATSSource facade."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx

from jobfeed.adapters.sources.ats import SUPPORTED_VENDORS, ATSSource
from jobfeed.config import SourcesATSConfig
from jobfeed.domain.models import CompanyRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLUG = "acme"
NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)
STALE_TIME = NOW - timedelta(days=30)
FRESH_TIME = NOW - timedelta(hours=1)

GH_PROBE_URL = f"https://boards-api.greenhouse.io/v1/boards/{SLUG}"

GH_CONTENT = "<p>Build distributed systems at scale. " + "A" * 200 + "</p>"

HTTP_200 = 200
HTTP_404 = 404
HTTP_410 = 410
HTTP_403 = 403
HTTP_429 = 429
HTTP_500 = 500

EXPECTED_MULTI_COMPANY_JOBS = 2
EXPECTED_CONCURRENT_JOBS = 3
EXPECTED_THRESHOLD_COUNTER = 2


# ---------------------------------------------------------------------------
# Mock store
# ---------------------------------------------------------------------------


class MockStore:
    """In-memory StoreOpsMixin implementation for tests."""

    def __init__(self) -> None:
        self.companies: dict[str, CompanyRecord] = {}
        self.removed: set[str] = set()
        self.failure_counts: dict[str, int] = {}

    def seed(self, *records: CompanyRecord) -> None:
        """Add company records to the store."""
        for rec in records:
            self.companies[rec.slug] = rec
            self.failure_counts.setdefault(rec.slug, rec.consecutive_discover_failures)

    async def upsert_company(self, company: CompanyRecord) -> None:
        """Upsert company record."""
        self.companies[company.slug] = company

    async def get_company(self, slug: str) -> CompanyRecord | None:
        """Get company by slug."""
        return self.companies.get(slug)

    async def list_companies(
        self,
        *,
        vendor: str | None = None,
        include_removed: bool = False,
    ) -> list[CompanyRecord]:
        """List companies with optional filters."""
        result = []
        for c in self.companies.values():
            if not include_removed and c.slug in self.removed:
                continue
            if vendor is not None and c.ats_vendor != vendor:
                continue
            result.append(c)
        return result

    async def mark_company_removed(self, slug: str) -> bool:
        """Soft-delete company."""
        if slug in self.companies:
            self.removed.add(slug)
            existing = self.companies[slug]
            self.companies[slug] = replace(existing, ats_vendor="removed")
            return True
        return False

    async def bump_discover_failure(self, slug: str) -> int:
        """Bump failure counter."""
        count = self.failure_counts.get(slug, 0) + 1
        self.failure_counts[slug] = count
        if slug in self.companies:
            existing = self.companies[slug]
            self.companies[slug] = replace(
                existing, consecutive_discover_failures=count
            )
        return count

    async def reset_discover_failures(self, slug: str) -> None:
        """Reset failure counter."""
        self.failure_counts[slug] = 0
        if slug in self.companies:
            existing = self.companies[slug]
            self.companies[slug] = replace(existing, consecutive_discover_failures=0)

    # Stubs for other StoreOpsMixin methods (unused by ATSSource)
    async def record_enrichment(self, **_kw: Any) -> None:
        """Stub."""

    async def enrich_paste(self, **_kw: Any) -> str:
        """Stub."""
        return ""

    async def get_state(self, _key: str) -> str | None:
        """Stub."""
        return None

    async def set_state(self, _key: str, _value: str) -> None:
        """Stub."""

    async def record_cost(self, **_kw: Any) -> None:
        """Stub."""

    async def get_cost(self, _day: str) -> None:
        """Stub."""

    async def get_cost_range(self, **_kw: Any) -> list:
        """Stub."""
        return []

    async def digest_stats(self, **_kw: Any) -> None:
        """Stub."""

    async def needs_attention(self, **_kw: Any) -> None:
        """Stub."""


# ---------------------------------------------------------------------------
# Mock logger
# ---------------------------------------------------------------------------


class MockLogger:
    """Captures log calls for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        """Record info event."""
        self.events.append(("info", event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:
        """Record error event."""
        self.events.append(("error", event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        """Record warning event."""
        self.events.append(("warning", event, kwargs))

    def debug(self, event: str, **kwargs: object) -> None:
        """Record debug event."""
        self.events.append(("debug", event, kwargs))

    def has_event(self, event_name: str) -> bool:
        """Check if an event was logged."""
        return any(e[1] == event_name for e in self.events)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _gh_job(job_id: int = 1001) -> dict[str, object]:
    """Build a synthetic Greenhouse job dict."""
    return {
        "id": job_id,
        "title": "Backend Engineer",
        "absolute_url": f"https://boards.greenhouse.io/{SLUG}/jobs/{job_id}",
        "location": {"name": "San Francisco, CA"},
        "content": GH_CONTENT,
        "updated_at": "2026-05-01T10:00:00Z",
    }


def _company(
    slug: str = SLUG,
    vendor: str | None = "greenhouse",
    verified_at: datetime | None = FRESH_TIME,
    **kwargs: object,
) -> CompanyRecord:
    """Build a CompanyRecord with defaults."""
    return CompanyRecord(
        slug=slug,
        ats_vendor=vendor,
        last_verified_at=verified_at,
        **kwargs,
    )


def _make_source(
    store: MockStore | None = None,
    config: SourcesATSConfig | None = None,
    logger: MockLogger | None = None,
) -> tuple[ATSSource, MockStore, MockLogger]:
    """Build ATSSource with injectable mocks."""
    mock_store = store or MockStore()
    mock_logger = logger or MockLogger()
    cfg = config or SourcesATSConfig(failure_threshold=3, probe_ttl_days=7)
    client = httpx.AsyncClient()
    # Pin the source's clock to the same fixed NOW the fixtures use, so
    # freshness/probe-TTL checks are deterministic and never drift against the
    # real wall clock (the cause of the 2026-05-30 failures).
    source = ATSSource(
        client=client,
        store=mock_store,
        config=cfg,
        logger=mock_logger,
        now=lambda: NOW,
    )
    return source, mock_store, mock_logger


def _gh_jobs_url(slug: str = SLUG) -> str:
    """Build Greenhouse jobs URL for a slug."""
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def _mock_gh_success(
    slug: str = SLUG, jobs: list[dict[str, object]] | None = None
) -> None:
    """Mock a successful Greenhouse fetch."""
    payload = {"jobs": jobs if jobs is not None else [_gh_job()]}
    respx.get(_gh_jobs_url(slug)).mock(
        return_value=httpx.Response(HTTP_200, json=payload)
    )


def _mock_gh_error(slug: str = SLUG, status: int = HTTP_404) -> None:
    """Mock a failed Greenhouse fetch."""
    respx.get(_gh_jobs_url(slug)).mock(return_value=httpx.Response(status))


def _probe_urls(slug: str = SLUG) -> tuple[str, str, str]:
    """Return (gh, ashby, lever) probe URLs for a slug."""
    gh = f"https://boards-api.greenhouse.io/v1/boards/{slug}"
    ashby = (
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )
    lever = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    return gh, ashby, lever


def _mock_all_probes_miss(slug: str = SLUG) -> None:
    """All three vendor probes return 404."""
    gh, ashby, lever = _probe_urls(slug)
    respx.head(gh).mock(return_value=httpx.Response(HTTP_404))
    respx.get(ashby).mock(return_value=httpx.Response(HTTP_404))
    respx.get(lever).mock(return_value=httpx.Response(HTTP_404))


def _mock_all_probes_timeout(slug: str = SLUG) -> None:
    """All three vendor probes time out."""
    gh, ashby, lever = _probe_urls(slug)
    respx.head(gh).mock(side_effect=httpx.TimeoutException("timeout"))
    respx.get(ashby).mock(side_effect=httpx.TimeoutException("timeout"))
    respx.get(lever).mock(side_effect=httpx.TimeoutException("timeout"))


# ---------------------------------------------------------------------------
# Tests: Basic fetch
# ---------------------------------------------------------------------------


class TestBasicFetch:
    """Test basic successful fetch flows."""

    @respx.mock
    async def test_fetch_known_vendor_returns_jobs(self) -> None:
        """Known greenhouse vendor fetches and returns jobs."""
        source, store, _log = _make_source()
        store.seed(_company())
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1
        assert jobs[0].platform == "greenhouse"
        assert jobs[0].title == "Backend Engineer"

    @respx.mock
    async def test_success_resets_failures_and_updates_count(self) -> None:
        """Successful fetch resets failure counter and updates scan count."""
        source, store, _log = _make_source()
        store.seed(_company(consecutive_discover_failures=2))
        _mock_gh_success()

        await source.fetch_jobs({})

        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.consecutive_discover_failures == 0
        assert rec.job_count_last_scan == 1
        assert rec.last_verified_at is not None

    @respx.mock
    async def test_empty_jobs_still_succeeds(self) -> None:
        """200 response with empty jobs list still counts as success."""
        source, store, _log = _make_source()
        store.seed(_company())
        _mock_gh_success(jobs=[])

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.consecutive_discover_failures == 0
        assert rec.job_count_last_scan == 0

    @respx.mock
    async def test_unsupported_vendor_silently_skipped(self) -> None:
        """Companies with unsupported vendors are silently skipped."""
        source, store, _log = _make_source()
        store.seed(_company(vendor="workday"))

        jobs = await source.fetch_jobs({})

        assert jobs == []

    @respx.mock
    async def test_multiple_companies_concurrent(self) -> None:
        """Multiple companies are fetched concurrently."""
        source, store, _log = _make_source()
        store.seed(_company(slug="alpha"), _company(slug="beta"))
        _mock_gh_success(slug="alpha")
        _mock_gh_success(slug="beta")

        jobs = await source.fetch_jobs({})

        assert len(jobs) == EXPECTED_MULTI_COMPANY_JOBS


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test per-company error handling paths."""

    @respx.mock
    async def test_404_bumps_failure_below_threshold(self) -> None:
        """404 bumps failure counter but does not trigger recovery."""
        source, store, logger = _make_source()
        store.seed(_company())
        _mock_gh_error(status=HTTP_404)

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.consecutive_discover_failures == 1
        assert logger.has_event("dead_board_below_threshold")

    @respx.mock
    async def test_410_bumps_failure_below_threshold(self) -> None:
        """410 bumps failure counter similar to 404."""
        source, store, _log = _make_source()
        store.seed(_company())
        _mock_gh_error(status=HTTP_410)

        jobs = await source.fetch_jobs({})

        assert jobs == []
        assert store.failure_counts[SLUG] == 1

    @respx.mock
    async def test_timeout_does_not_bump(self) -> None:
        """Timeout (status_code=None) does not bump failure counter."""
        source, store, logger = _make_source()
        store.seed(_company())
        respx.get(_gh_jobs_url()).mock(side_effect=httpx.TimeoutException("timeout"))

        jobs = await source.fetch_jobs({})

        assert jobs == []
        assert store.failure_counts.get(SLUG, 0) == 0
        assert logger.has_event("network_error")

    @respx.mock
    async def test_403_does_not_bump(self) -> None:
        """Non-dead HTTP errors (403, 429, 5xx) do not bump."""
        source, store, logger = _make_source()
        store.seed(_company())
        _mock_gh_error(status=HTTP_403)

        jobs = await source.fetch_jobs({})

        assert jobs == []
        assert store.failure_counts.get(SLUG, 0) == 0
        assert logger.has_event("http_error")

    @respx.mock
    async def test_429_does_not_bump(self) -> None:
        """429 does not bump failure counter."""
        source, store, _log = _make_source()
        store.seed(_company())
        _mock_gh_error(status=HTTP_429)

        jobs = await source.fetch_jobs({})

        assert jobs == []
        assert store.failure_counts.get(SLUG, 0) == 0

    @respx.mock
    async def test_parse_error_does_not_bump(self) -> None:
        """ATSParseError does not bump failure counter."""
        source, store, logger = _make_source()
        store.seed(_company())
        respx.get(_gh_jobs_url()).mock(
            return_value=httpx.Response(HTTP_200, json={"not_jobs": []})
        )

        jobs = await source.fetch_jobs({})

        assert jobs == []
        assert store.failure_counts.get(SLUG, 0) == 0
        assert logger.has_event("parse_error")

    @respx.mock
    async def test_never_raises_for_company_failure(self) -> None:
        """fetch_jobs never raises even if all companies fail."""
        source, store, _log = _make_source()
        store.seed(_company(slug="a"), _company(slug="b"))
        _mock_gh_error(slug="a", status=HTTP_500)
        _mock_gh_error(slug="b", status=HTTP_500)

        jobs = await source.fetch_jobs({})

        assert jobs == []

    @respx.mock
    async def test_one_failure_does_not_cancel_others(self) -> None:
        """One company failing does not affect other companies."""
        source, store, _log = _make_source()
        store.seed(_company(slug="good"), _company(slug="bad"))
        _mock_gh_success(slug="good")
        _mock_gh_error(slug="bad", status=HTTP_500)

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Tests: Dead board recovery
# ---------------------------------------------------------------------------


class TestDeadBoardRecovery:
    """Test 404/410 threshold and resolve_dead_slug recovery."""

    @respx.mock
    async def test_at_threshold_resolves_and_removes_if_dead(self) -> None:
        """At failure threshold, probe all vendors. If all miss, remove."""
        source, store, logger = _make_source(
            config=SourcesATSConfig(failure_threshold=2)
        )
        store.seed(_company(consecutive_discover_failures=1))
        _mock_gh_error(status=HTTP_404)
        _mock_all_probes_miss()

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor == "removed"
        assert rec.consecutive_discover_failures == 0
        assert logger.has_event("company_removed")

    @respx.mock
    async def test_recovery_with_same_vendor(self) -> None:
        """At threshold, resolve finds same vendor, retry succeeds."""
        source, store, _log = _make_source(config=SourcesATSConfig(failure_threshold=2))
        store.seed(_company(consecutive_discover_failures=1))

        respx.get(_gh_jobs_url()).mock(
            side_effect=[
                httpx.Response(HTTP_404),
                httpx.Response(HTTP_200, json={"jobs": [_gh_job()]}),
            ]
        )
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.consecutive_discover_failures == 0

    @respx.mock
    async def test_recovery_retry_404_leaves_counter(self) -> None:
        """Recovery found vendor but retry still 404: leave counter."""
        source, store, _log = _make_source(config=SourcesATSConfig(failure_threshold=2))
        store.seed(_company(consecutive_discover_failures=1))

        respx.get(_gh_jobs_url()).mock(return_value=httpx.Response(HTTP_404))
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))

        jobs = await source.fetch_jobs({})

        assert jobs == []
        assert store.failure_counts[SLUG] == EXPECTED_THRESHOLD_COUNTER

    @respx.mock
    async def test_recovery_probe_network_error_leaves(self) -> None:
        """ProbeNetworkError during resolve does not mark removed."""
        source, store, logger = _make_source(
            config=SourcesATSConfig(failure_threshold=2)
        )
        store.seed(_company(consecutive_discover_failures=1))
        _mock_gh_error(status=HTTP_404)
        _mock_all_probes_timeout()

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor == "greenhouse"
        assert logger.has_event("resolve_unresolved")

    @respx.mock
    async def test_recovery_probe_indeterminate_leaves(self) -> None:
        """ProbeIndeterminateError during resolve does not mark removed."""
        source, store, _log = _make_source(config=SourcesATSConfig(failure_threshold=2))
        store.seed(_company(consecutive_discover_failures=1))
        _mock_gh_error(status=HTTP_404)
        gh, ashby, lever = _probe_urls()
        respx.head(gh).mock(return_value=httpx.Response(HTTP_500))
        respx.get(ashby).mock(return_value=httpx.Response(HTTP_404))
        respx.get(lever).mock(return_value=httpx.Response(HTTP_404))

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor == "greenhouse"


# ---------------------------------------------------------------------------
# Tests: Probe for unknown vendor
# ---------------------------------------------------------------------------


class TestProbeUnknown:
    """Test probing for companies with ats_vendor=None."""

    @respx.mock
    async def test_probe_finds_vendor_and_fetches(self) -> None:
        """Unknown vendor company probes, finds greenhouse, fetches."""
        source, store, _log = _make_source()
        store.seed(_company(vendor=None, verified_at=None))
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor == "greenhouse"

    @respx.mock
    async def test_probe_all_miss_skips_no_failure(self) -> None:
        """All probes miss for unknown vendor: skip, no failure bump."""
        source, store, _log = _make_source()
        store.seed(_company(vendor=None, verified_at=None))
        _mock_all_probes_miss()

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor is None
        assert rec.last_verified_at is not None
        assert rec.last_probe_attempt_at is not None
        assert store.failure_counts.get(SLUG, 0) == 0

    @respx.mock
    async def test_probe_network_error_skips(self) -> None:
        """Probe network error: update attempt time only."""
        source, store, logger = _make_source()
        store.seed(_company(vendor=None, verified_at=None))
        _mock_all_probes_timeout()

        jobs = await source.fetch_jobs({})

        assert jobs == []
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor is None
        assert rec.last_verified_at is None
        assert rec.last_probe_attempt_at is not None
        assert logger.has_event("probe_error")

    @respx.mock
    async def test_fresh_ttl_skips_probe(self) -> None:
        """Unknown vendor with fresh verified_at skips probe entirely."""
        source, _store, _log = _make_source()
        _store.seed(_company(vendor=None, verified_at=FRESH_TIME))

        jobs = await source.fetch_jobs({})

        assert jobs == []


# ---------------------------------------------------------------------------
# Tests: Known vendor TTL refresh
# ---------------------------------------------------------------------------


class TestKnownVendorRefresh:
    """Test TTL-based re-probing for known vendors."""

    @respx.mock
    async def test_stale_ttl_reprobes_same_vendor(self) -> None:
        """Stale vendor re-probed, same vendor confirmed, fetch proceeds."""
        source, store, _log = _make_source()
        store.seed(_company(verified_at=STALE_TIME))
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor == "greenhouse"

    @respx.mock
    async def test_stale_probe_returns_none_uses_cached(self) -> None:
        """Stale probe returns None: use cached vendor anyway."""
        source, store, _log = _make_source()
        store.seed(_company(verified_at=STALE_TIME))
        _mock_all_probes_miss()
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1

    @respx.mock
    async def test_stale_probe_error_uses_cached(self) -> None:
        """Stale probe raises error: use cached vendor and proceed."""
        source, store, _log = _make_source()
        store.seed(_company(verified_at=STALE_TIME))
        _mock_all_probes_timeout()
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1

    @respx.mock
    async def test_fresh_ttl_skips_reprobe(self) -> None:
        """Fresh verified_at skips re-probe entirely."""
        source, store, _log = _make_source()
        store.seed(_company(verified_at=FRESH_TIME))
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Tests: Override companies
# ---------------------------------------------------------------------------


class TestOverride:
    """Test ats_override=True behavior."""

    @respx.mock
    async def test_override_skips_reprobe_when_stale(self) -> None:
        """ats_override=True trusts cached vendor even when stale."""
        source, store, _log = _make_source()
        store.seed(_company(verified_at=STALE_TIME, ats_override=True))
        _mock_gh_success()

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1

    @respx.mock
    async def test_override_still_triggers_recovery(self) -> None:
        """ats_override=True still runs resolve_dead_slug at threshold."""
        source, store, _log = _make_source(config=SourcesATSConfig(failure_threshold=2))
        store.seed(_company(consecutive_discover_failures=1, ats_override=True))
        respx.get(_gh_jobs_url()).mock(
            side_effect=[
                httpx.Response(HTTP_404),
                httpx.Response(HTTP_200, json={"jobs": [_gh_job()]}),
            ]
        )
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))

        jobs = await source.fetch_jobs({})

        assert len(jobs) == 1
        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.consecutive_discover_failures == 0


# ---------------------------------------------------------------------------
# Tests: Vendor filtering
# ---------------------------------------------------------------------------


class TestVendorFiltering:
    """Test SUPPORTED_VENDORS filtering."""

    def test_supported_vendors_set(self) -> None:
        """SUPPORTED_VENDORS contains expected vendors."""
        expected = frozenset({"greenhouse", "ashby", "lever"})
        assert expected == SUPPORTED_VENDORS

    @respx.mock
    async def test_removed_vendor_skipped(self) -> None:
        """Companies with vendor='removed' are skipped."""
        source, store, _log = _make_source()
        store.seed(_company(vendor="removed"))

        jobs = await source.fetch_jobs({})

        assert jobs == []

    @respx.mock
    async def test_unknown_string_vendor_skipped(self) -> None:
        """Companies with unrecognized vendor string are skipped."""
        source, store, _log = _make_source()
        store.seed(_company(vendor="taleo"))

        jobs = await source.fetch_jobs({})

        assert jobs == []


# ---------------------------------------------------------------------------
# Tests: Read-modify-write pattern
# ---------------------------------------------------------------------------


class TestReadModifyWrite:
    """Test that ATSSource uses read-modify-write, not fresh partials."""

    @respx.mock
    async def test_success_preserves_notes(self) -> None:
        """Success update preserves unrelated fields like notes."""
        source, store, _log = _make_source()
        store.seed(_company(notes="important note"))
        _mock_gh_success()

        await source.fetch_jobs({})

        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.notes == "important note"
        assert rec.job_count_last_scan == 1

    @respx.mock
    async def test_probe_hit_preserves_override(self) -> None:
        """Probe result preserves ats_override flag."""
        source, store, _log = _make_source()
        store.seed(_company(vendor=None, verified_at=None, ats_override=True))
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))
        _mock_gh_success()

        await source.fetch_jobs({})

        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_vendor == "greenhouse"
        assert rec.ats_override is True


# ---------------------------------------------------------------------------
# Tests: Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Test semaphore concurrency limiting."""

    @respx.mock
    async def test_semaphore_limits_concurrent(self) -> None:
        """max_concurrent=1 processes companies sequentially."""
        config = SourcesATSConfig(max_concurrent=1, failure_threshold=3)
        source, store, _log = _make_source(config=config)
        store.seed(_company(slug="a"), _company(slug="b"), _company(slug="c"))
        _mock_gh_success(slug="a")
        _mock_gh_success(slug="b")
        _mock_gh_success(slug="c")

        jobs = await source.fetch_jobs({})

        assert len(jobs) == EXPECTED_CONCURRENT_JOBS


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test various edge cases."""

    @respx.mock
    async def test_no_companies_returns_empty(self) -> None:
        """No companies in store returns empty list."""
        source, _store, _log = _make_source()

        jobs = await source.fetch_jobs({})

        assert jobs == []

    @respx.mock
    async def test_all_companies_unsupported_returns_empty(self) -> None:
        """All companies with unsupported vendors returns empty."""
        source, store, _log = _make_source()
        store.seed(
            _company(slug="a", vendor="workday"),
            _company(slug="b", vendor="icims"),
        )

        jobs = await source.fetch_jobs({})

        assert jobs == []

    @respx.mock
    async def test_recovery_preserves_override_flag(self) -> None:
        """Recovery at threshold preserves ats_override on the record."""
        source, store, _log = _make_source(config=SourcesATSConfig(failure_threshold=2))
        store.seed(
            _company(
                consecutive_discover_failures=1,
                ats_override=True,
                notes="keep me",
            )
        )
        respx.get(_gh_jobs_url()).mock(
            side_effect=[
                httpx.Response(HTTP_404),
                httpx.Response(HTTP_200, json={"jobs": [_gh_job()]}),
            ]
        )
        respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))

        await source.fetch_jobs({})

        rec = await store.get_company(SLUG)
        assert rec is not None
        assert rec.ats_override is True
        assert rec.notes == "keep me"
