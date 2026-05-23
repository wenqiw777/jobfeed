"""Integration tests for the ATS scan chain with real PG and mocked HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
import structlog

from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.config import SourcesATSConfig
from jobfeed.domain.models import CompanyRecord
from jobfeed.services.scan import ScanService

pytestmark = pytest.mark.postgres

# ---------------------------------------------------------------------------
# Shared constants and fixtures
# ---------------------------------------------------------------------------

_RECENT_HOURS = 1
_GH_JOB_COUNT = 2
_ASHBY_JOB_COUNT = 2
_TOTAL_HAPPY_PATH = _GH_JOB_COUNT + _ASHBY_JOB_COUNT
_FAILURE_THRESHOLD = 3

# Greenhouse fixture: two jobs
_GH_JOBS_RESPONSE: dict[str, object] = {
    "jobs": [
        {
            "id": 50001,
            "title": "Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/50001",
            "location": {"name": "San Francisco, CA"},
            "content": "<p>Join our backend team to build scalable APIs. "
            "We use Python and Go in production, with PostgreSQL and Redis. "
            "You will design and ship features used by millions daily. "
            "Strong distributed systems background required.</p>",
            "updated_at": "2026-05-10T14:30:00Z",
            "company_name": "Acme Inc",
        },
        {
            "id": 50002,
            "title": "Frontend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/50002",
            "location": {"name": "Remote"},
            "content": "<p>Build beautiful UIs with React and TypeScript. "
            "Collaborate with designers to deliver accessible web apps. "
            "Experience with performance optimization and testing required. "
            "We ship fast with continuous integration.</p>",
            "updated_at": "2026-05-11T10:00:00Z",
        },
    ]
}

# Ashby fixture: two jobs
_ASHBY_JOBS_RESPONSE: dict[str, object] = {
    "jobs": [
        {
            "id": "ash-uuid-001",
            "title": "ML Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/betacorp/ash-uuid-001",
            "location": "New York, NY",
            "descriptionPlain": "Build production ML models for our recommendation "
            "engine. Experience with PyTorch and Kubernetes required. "
            "Work closely with data scientists to ship models at scale. "
            "Strong engineering fundamentals expected.",
            "publishedAt": "2026-05-01T08:00:00Z",
        },
        {
            "id": "ash-uuid-002",
            "title": "Data Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/betacorp/ash-uuid-002",
            "location": "Remote - US",
            "descriptionPlain": "Design and maintain data pipelines using Spark "
            "and dbt. Ensure data quality with automated testing. "
            "Collaborate with analytics and ML teams to deliver "
            "reliable data infrastructure at scale.",
            "publishedAt": "2026-05-03T10:30:00Z",
        },
    ],
    "apiVersion": "v1",
}

# Vendor endpoint URL templates
_GH_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
_GH_PROBE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"
_ASHBY_URL = (
    "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
)
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def _recent() -> datetime:
    """Return a recent timestamp for last_verified_at."""
    return datetime.now(UTC) - timedelta(hours=_RECENT_HOURS)


def _logger() -> structlog.stdlib.BoundLogger:
    """Return a logger for tests."""
    return structlog.get_logger()


def _make_ats_source(
    client: httpx.AsyncClient,
    store: PostgresStore,
    *,
    failure_threshold: int = _FAILURE_THRESHOLD,
) -> ATSSource:
    """Build an ATSSource wired to the given client and store."""
    config = SourcesATSConfig(
        failure_threshold=failure_threshold,
        seed_companies=[],
        probe_ttl_days=7,
    )
    return ATSSource(
        client=client,
        store=store,
        config=config,
        logger=_logger(),
    )


# ---------------------------------------------------------------------------
# Scenario 1: Happy path scan
# ---------------------------------------------------------------------------


@respx.mock
async def test_happy_path_scan(store: PostgresStore) -> None:
    """Two companies (GH + Ashby) scan successfully via ScanService."""
    await store.upsert_company(
        CompanyRecord(
            slug="acme",
            ats_vendor="greenhouse",
            last_verified_at=_recent(),
        )
    )
    await store.upsert_company(
        CompanyRecord(
            slug="betacorp",
            ats_vendor="ashby",
            last_verified_at=_recent(),
        )
    )

    respx.get(_GH_JOBS_URL.format(slug="acme")).respond(200, json=_GH_JOBS_RESPONSE)
    respx.get(_ASHBY_URL.format(slug="betacorp")).respond(
        200, json=_ASHBY_JOBS_RESPONSE
    )

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        run = await service.run([("ats", ats_source, {})])

    jobs = await store.list_jobs()
    assert len(jobs) == _TOTAL_HAPPY_PATH

    acme = await store.get_company("acme")
    assert acme is not None
    assert acme.job_count_last_scan == _GH_JOB_COUNT
    assert acme.consecutive_discover_failures == 0

    betacorp = await store.get_company("betacorp")
    assert betacorp is not None
    assert betacorp.job_count_last_scan == _ASHBY_JOB_COUNT
    assert betacorp.consecutive_discover_failures == 0

    assert run.jobs_discovered == _TOTAL_HAPPY_PATH
    assert run.jobs_inserted == _TOTAL_HAPPY_PATH


# ---------------------------------------------------------------------------
# Scenario 2: Probe + scan (unknown vendor)
# ---------------------------------------------------------------------------


@respx.mock
async def test_probe_and_scan_unknown_vendor(store: PostgresStore) -> None:
    """Company with no cached vendor probed; Ashby hits, jobs saved."""
    await store.upsert_company(CompanyRecord(slug="newcorp", ats_vendor=None))

    respx.head(_GH_PROBE_URL.format(slug="newcorp")).respond(404)
    respx.get(_ASHBY_URL.format(slug="newcorp")).respond(200, json=_ASHBY_JOBS_RESPONSE)

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        run = await service.run([("ats", ats_source, {})])

    company = await store.get_company("newcorp")
    assert company is not None
    assert company.ats_vendor == "ashby"

    jobs = await store.list_jobs()
    assert len(jobs) == _ASHBY_JOB_COUNT
    assert run.jobs_discovered == _ASHBY_JOB_COUNT


# ---------------------------------------------------------------------------
# Scenario 3: Dead slug resolution
# ---------------------------------------------------------------------------


@respx.mock
async def test_dead_slug_resolution(store: PostgresStore) -> None:
    """At failure threshold, re-probes and migrates to new vendor."""
    await store.upsert_company(
        CompanyRecord(
            slug="migrating",
            ats_vendor="greenhouse",
            last_verified_at=_recent(),
            consecutive_discover_failures=2,
        )
    )

    respx.get(_GH_JOBS_URL.format(slug="migrating")).respond(404)
    respx.head(_GH_PROBE_URL.format(slug="migrating")).respond(404)
    respx.get(_ASHBY_URL.format(slug="migrating")).respond(
        200, json=_ASHBY_JOBS_RESPONSE
    )

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        await service.run([("ats", ats_source, {})])

    company = await store.get_company("migrating")
    assert company is not None
    assert company.ats_vendor == "ashby"
    assert company.consecutive_discover_failures == 0

    jobs = await store.list_jobs()
    assert len(jobs) == _ASHBY_JOB_COUNT


# ---------------------------------------------------------------------------
# Scenario 4: Definitive 404 below threshold
# ---------------------------------------------------------------------------


@respx.mock
async def test_definitive_404_below_threshold(store: PostgresStore) -> None:
    """404 below threshold increments counter; no re-probe attempted."""
    await store.upsert_company(
        CompanyRecord(
            slug="flaky",
            ats_vendor="greenhouse",
            last_verified_at=_recent(),
            consecutive_discover_failures=0,
        )
    )

    respx.get(_GH_JOBS_URL.format(slug="flaky")).respond(404)

    gh_probe = respx.head(_GH_PROBE_URL.format(slug="flaky"))
    ashby_probe = respx.get(_ASHBY_URL.format(slug="flaky"))

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        await service.run([("ats", ats_source, {})])

    company = await store.get_company("flaky")
    assert company is not None
    assert company.consecutive_discover_failures == 1
    assert company.ats_vendor == "greenhouse"

    assert not gh_probe.called
    assert not ashby_probe.called


# ---------------------------------------------------------------------------
# Scenario 5: Company failure threshold removal
# ---------------------------------------------------------------------------


@respx.mock
async def test_company_failure_threshold_removal(
    store: PostgresStore,
) -> None:
    """All vendors 404 at threshold marks company removed."""
    await store.upsert_company(
        CompanyRecord(
            slug="deadcorp",
            ats_vendor="greenhouse",
            last_verified_at=_recent(),
            consecutive_discover_failures=2,
        )
    )

    respx.get(_GH_JOBS_URL.format(slug="deadcorp")).respond(404)
    respx.head(_GH_PROBE_URL.format(slug="deadcorp")).respond(404)
    respx.get(_ASHBY_URL.format(slug="deadcorp")).respond(410)
    respx.get(_LEVER_URL.format(slug="deadcorp")).respond(404)

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        await service.run([("ats", ats_source, {})])

    removed = await store.list_companies(include_removed=True)
    dead = [c for c in removed if c.slug == "deadcorp"]
    assert len(dead) == 1
    assert dead[0].ats_vendor == "removed"
    assert dead[0].consecutive_discover_failures == 0


# ---------------------------------------------------------------------------
# Scenario 6: Idempotent re-scan
# ---------------------------------------------------------------------------


@respx.mock
async def test_idempotent_rescan(store: PostgresStore) -> None:
    """Second scan with same data: upsert, not duplicate."""
    await store.upsert_company(
        CompanyRecord(
            slug="stable",
            ats_vendor="greenhouse",
            last_verified_at=_recent(),
        )
    )

    respx.get(_GH_JOBS_URL.format(slug="stable")).respond(200, json=_GH_JOBS_RESPONSE)

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())

        run1 = await service.run([("ats", ats_source, {})])
        run2 = await service.run([("ats", ats_source, {})])

    jobs = await store.list_jobs()
    assert len(jobs) == _GH_JOB_COUNT

    assert run1.jobs_inserted == _GH_JOB_COUNT
    assert run2.jobs_inserted == 0
    assert run2.jobs_updated == _GH_JOB_COUNT


# ---------------------------------------------------------------------------
# Scenario 7: Mixed success/failure (error containment)
# ---------------------------------------------------------------------------


@respx.mock
async def test_mixed_success_failure(store: PostgresStore) -> None:
    """One succeeds, others fail variously; only 404 bumps failures."""
    slugs = ("goodco", "deadco", "slowco", "brokenco", "garbleco")
    for slug in slugs:
        await store.upsert_company(
            CompanyRecord(
                slug=slug,
                ats_vendor="greenhouse",
                last_verified_at=_recent(),
                consecutive_discover_failures=0,
            )
        )

    # goodco: 200 with jobs
    respx.get(_GH_JOBS_URL.format(slug="goodco")).respond(200, json=_GH_JOBS_RESPONSE)
    # deadco: 404
    respx.get(_GH_JOBS_URL.format(slug="deadco")).respond(404)
    # slowco: timeout
    respx.get(_GH_JOBS_URL.format(slug="slowco")).mock(
        side_effect=httpx.ReadTimeout("read timed out")
    )
    # brokenco: 500
    respx.get(_GH_JOBS_URL.format(slug="brokenco")).respond(500)
    # garbleco: 200 but malformed JSON
    respx.get(_GH_JOBS_URL.format(slug="garbleco")).respond(
        200,
        content=b"this is not json at all",
        headers={"content-type": "application/json"},
    )

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        run = await service.run([("ats", ats_source, {})])

    jobs = await store.list_jobs()
    assert len(jobs) == _GH_JOB_COUNT  # only goodco

    deadco = await store.get_company("deadco")
    assert deadco is not None
    assert deadco.consecutive_discover_failures == 1

    for slug in ("slowco", "brokenco", "garbleco"):
        company = await store.get_company(slug)
        assert company is not None
        msg = f"{slug} should stay at 0"
        assert company.consecutive_discover_failures == 0, msg

    # ATSSource contains all per-company errors internally
    assert run.errors == 0


# ---------------------------------------------------------------------------
# Scenario 8: Ambiguous dead-slug resolution does not remove
# ---------------------------------------------------------------------------


@respx.mock
async def test_ambiguous_dead_slug_no_removal(
    store: PostgresStore,
) -> None:
    """Ambiguous re-probe (429) prevents company removal."""
    await store.upsert_company(
        CompanyRecord(
            slug="ambicorp",
            ats_vendor="greenhouse",
            last_verified_at=_recent(),
            consecutive_discover_failures=2,
        )
    )

    respx.get(_GH_JOBS_URL.format(slug="ambicorp")).respond(404)
    respx.head(_GH_PROBE_URL.format(slug="ambicorp")).respond(404)
    respx.get(_ASHBY_URL.format(slug="ambicorp")).respond(429)
    # Lever may not be reached; route added for resilience
    respx.get(_LEVER_URL.format(slug="ambicorp")).respond(404)

    async with create_http_client() as client:
        ats_source = _make_ats_source(client, store)
        service = ScanService(store, _logger())
        await service.run([("ats", ats_source, {})])

    company = await store.get_company("ambicorp")
    assert company is not None
    assert company.ats_vendor != "removed"
    assert company.consecutive_discover_failures == _FAILURE_THRESHOLD
