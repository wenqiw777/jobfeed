"""Phase 4a integration tests: real PG + fully-mocked source IO.

Every scenario drives the EXISTING ``ScanService.run`` SimpleSource path with a
freshly migrated PostgreSQL schema (the ``store`` fixture) and asserts the rows
that actually land in the database. All network is mocked — respx for HTTP
(SpeedyApply markdown + ATS/greenhouse JD endpoints) and a monkeypatched JobSpy
process runner for the JobSpy sources — so nothing reaches the wire.

Scenarios (Phase 4a subset of plan Task 8):
  1. SpeedyApply happy path (routed + unrouted enrich_source labels).
  2. Indeed JobSpy (inline JD, posted_at from dateOnIndeed).
  5. Cross-source twin: same job from two sources persists as two rows; the
     dedupe primitive folds them and picks the greenhouse representative — once
     on a quality differential, once on the source-priority tiebreak.
  6. Idempotent re-scan (UPSERT, no duplicate rows).
  7. Mixed success/failure containment (per-item failure kept, run.errors == 0).

LinkedIn SessionSource (Scenario 3) and the needs_reauth / EnrichLocked
source-level failures (Scenarios 4/4b) are Phase 4b and are NOT covered here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
import structlog

from jobfeed.adapters.sources import _jobspy, _jobspy_process
from jobfeed.adapters.sources._ats_greenhouse import JOB_URL as GH_JOB_URL
from jobfeed.adapters.sources._ats_greenhouse import JOBS_URL as GH_JOBS_URL
from jobfeed.adapters.sources._http import create_http_client
from jobfeed.adapters.sources._speedyapply_markdown import canonical_id_for
from jobfeed.adapters.sources.ats import ATSSource
from jobfeed.adapters.sources.indeed_jobspy import IndeedSource
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.config import (
    SourcesATSConfig,
    SourcesIndeedConfig,
    SourcesSpeedyApplyConfig,
)
from jobfeed.domain.dedupe import cluster_twins
from jobfeed.domain.models import CompanyRecord, JobPosting, QualityBand
from jobfeed.services.scan import ScanService

pytestmark = pytest.mark.postgres

# ---------------------------------------------------------------------------
# Shared helpers / constants
# ---------------------------------------------------------------------------

_README_URL = "https://lists.example.test/speedyapply.md"
# A JD body comfortably over the 1000-char FULL threshold.
_FULL_JD = "Build and operate large-scale backend services. " * 30

# Named counts so assertions avoid PLR2004 magic-value warnings.
_TWO_ROWS = 2
_ONE_ROW = 1
_ONE_CLUSTER = 1
_TWO_MEMBERS = 2


def _logger() -> structlog.stdlib.BoundLogger:
    """Return a logger for tests."""
    return structlog.get_logger()


def _recent() -> datetime:
    """Recent timestamp so ATSSource scans the company without re-probing."""
    return datetime.now(UTC) - timedelta(hours=1)


def _gh_job(
    *, job_id: int, title: str, company: str, content: str, slug: str
) -> dict[str, Any]:
    """Build one Greenhouse jobs-endpoint job object."""
    return {
        "id": job_id,
        "title": title,
        "absolute_url": f"https://boards.greenhouse.io/{slug}/jobs/{job_id}",
        "location": {"name": "Remote"},
        "content": content,
        "updated_at": "2026-05-20T10:00:00Z",
        "company_name": company,
    }


def _speedyapply_config() -> SourcesSpeedyApplyConfig:
    """SpeedyApply config pinned to the mocked README URL."""
    return SourcesSpeedyApplyConfig(enabled=True, search_urls=[_README_URL])


def _speedyapply_source(client: httpx.AsyncClient) -> SpeedyApplySource:
    """Build a SpeedyApplySource wired to the given client."""
    return SpeedyApplySource(
        client=client, config=_speedyapply_config(), logger=_logger()
    )


def _ats_source(client: httpx.AsyncClient, store: PostgresStore) -> ATSSource:
    """Build an ATSSource over already-seeded companies."""
    config = SourcesATSConfig(failure_threshold=3, seed_companies=[], probe_ttl_days=7)
    return ATSSource(client=client, store=store, config=config, logger=_logger())


async def _seed_gh_company(store: PostgresStore, slug: str) -> None:
    """Seed a recently-verified Greenhouse company so ATSSource scans it."""
    await store.upsert_company(
        CompanyRecord(slug=slug, ats_vendor="greenhouse", last_verified_at=_recent())
    )


def _install_fake_scrape(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    """Replace the JobSpy process runner with one converting ``frame``."""

    def _fake(
        request: _jobspy_process._ScrapeRequest, _timeout_s: float
    ) -> _jobspy_process._ScrapeProcessOutcome:
        return _jobspy_process._ScrapeProcessOutcome(
            postings=_jobspy._frame_to_postings(
                frame,
                platform=request.platform,
                discovered_at=request.discovered_at,
            )
        )

    monkeypatch.setattr(_jobspy_process, "_run_scrape_process", _fake)


def _readme(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Render a minimal 5-col speedyapply markdown table.

    Each row tuple is (company, position, location, apply_url, age).
    """
    header = "| Company | Position | Location | Posting | Age |\n"
    sep = "|---|---|---|---|---|\n"
    body = "".join(
        f"| <strong>{co}</strong> | {pos} | {loc} | "
        f'<a href="{url}">Apply</a> | {age} |\n'
        for co, pos, loc, url, age in rows
    )
    return header + sep + body


# ===========================================================================
# Scenario 1: SpeedyApply happy path (routed + unrouted)
# ===========================================================================


@respx.mock
async def test_speedyapply_happy_path(store: PostgresStore) -> None:
    """Routed greenhouse row + unrouted row persist with correct enrich_source."""
    gh_apply = "https://boards.greenhouse.io/acmeco/jobs/7001"
    unrouted_apply = "https://careers.globex.example.com/postings/swe-001"
    readme = _readme(
        [
            ("Acme Co", "Software Engineer Intern", "SF, CA", gh_apply, "2d"),
            ("Globex", "Backend Engineer Intern", "Remote", unrouted_apply, "3d"),
        ]
    )
    respx.get(_README_URL).respond(200, text=readme)
    # The routed greenhouse single-job endpoint (full URL incl. ?content=true).
    respx.get(GH_JOB_URL.format(slug="acmeco", job_id="7001")).respond(
        200,
        json=_gh_job(
            job_id=7001,
            title="SWE Intern",
            company="Acme Co",
            content=f"<p>{_FULL_JD}</p>",
            slug="acmeco",
        ),
    )

    async with create_http_client() as client:
        service = ScanService(store, _logger())
        run = await service.run([("speedyapply", _speedyapply_source(client), {})])

    jobs = await store.list_jobs()
    assert len(jobs) == _TWO_ROWS
    assert all(j.platform == "speedyapply" for j in jobs)

    by_source = {j.enrich_source for j in jobs}
    assert by_source == {"speedyapply-greenhouse", "speedyapply-unrouted"}

    routed = next(j for j in jobs if j.enrich_source == "speedyapply-greenhouse")
    assert routed.jd_quality == QualityBand.FULL
    assert routed.jd_text is not None

    unrouted = next(j for j in jobs if j.enrich_source == "speedyapply-unrouted")
    assert unrouted.jd_quality == QualityBand.MISSING
    assert unrouted.jd_text is None

    assert run.jobs_discovered == _TWO_ROWS
    assert run.jobs_inserted == _TWO_ROWS
    assert run.errors == 0


# ===========================================================================
# Scenario 2: Indeed JobSpy (inline JD, posted_at from dateOnIndeed)
# ===========================================================================


async def test_indeed_jobspy_inline_jd_and_date_patch(
    store: PostgresStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indeed rows persist with inline JD and posted_at from dateOnIndeed."""
    # posted_at must follow dateOnIndeed (the date-patch path), NOT the older
    # datePublished. The fake scrape returns date_posted=dateOnIndeed, which is
    # exactly what JobSpy emits once apply_indeed_date_patch is active. The
    # source still invokes apply_indeed_date_patch() before scraping.
    indexed_new = "2026-05-28"
    frame = pd.DataFrame(
        [
            {
                "id": "in-100",
                "title": "Software Engineer Intern",
                "company": "Wayne Enterprises",
                "location": "Gotham, NJ",
                "job_url": "https://www.indeed.com/viewjob?jk=100",
                "description": _FULL_JD,
                "date_posted": indexed_new,
            }
        ]
    )
    _install_fake_scrape(monkeypatch, frame)

    config = SourcesIndeedConfig(
        enabled=True, search_urls=["https://indeed/q?fromage=3"]
    )
    source = IndeedSource(config=config, logger=_logger())

    service = ScanService(store, _logger())
    run = await service.run([("indeed", source, {})])

    jobs = await store.list_jobs()
    assert len(jobs) == _ONE_ROW
    job = jobs[0]
    assert job.platform == "indeed"
    assert job.canonical_id == "in-100"
    assert job.jd_text is not None
    assert job.enrich_source == "jobspy_inline"
    # posted_at reflects dateOnIndeed, not the (older) datePublished.
    assert job.posted_at == datetime(2026, 5, 28, tzinfo=UTC)
    assert run.jobs_inserted == _ONE_ROW
    assert run.errors == 0


# ===========================================================================
# Scenario 5: Cross-source twin (the dedupe showcase)
# ===========================================================================


@respx.mock
async def test_cross_source_twin_quality_differential(store: PostgresStore) -> None:
    """Same job from greenhouse (FULL JD) + speedyapply (unrouted, MISSING JD).

    BOTH rows persist (no scan-time dedup). ``cluster_twins`` over the persisted
    rows yields ONE cluster whose representative is the greenhouse FULL-JD row
    (rule 1: highest JD quality wins over the MISSING speedyapply twin).
    """
    await _seed_gh_company(store, "stripeats")
    respx.get(GH_JOBS_URL.format(slug="stripeats")).respond(
        200,
        json={
            "jobs": [
                _gh_job(
                    job_id=9100,
                    title="Backend Engineer",
                    company="Stripe",
                    content=f"<p>{_FULL_JD}</p>",
                    slug="stripeats",
                )
            ]
        },
    )
    # SpeedyApply twin: same company+title, unrouted apply URL → MISSING JD.
    readme = _readme(
        [
            (
                "Stripe",
                "Backend Engineer",
                "Remote",
                "https://careers.stripe.example.com/apply/be-1",
                "1d",
            )
        ]
    )
    respx.get(_README_URL).respond(200, text=readme)

    async with create_http_client() as client:
        service = ScanService(store, _logger())
        await service.run(
            [
                ("ats", _ats_source(client, store), {}),
                ("speedyapply", _speedyapply_source(client), {}),
            ]
        )

    jobs = await store.list_jobs()
    # No scan-time dedup: both source rows are persisted.
    assert len(jobs) == _TWO_ROWS
    assert {j.platform for j in jobs} == {"greenhouse", "speedyapply"}

    clusters = cluster_twins(jobs)
    assert len(clusters) == _ONE_CLUSTER
    cluster = clusters[0]
    assert len(cluster.members) == _TWO_MEMBERS
    # Quality differential: greenhouse FULL beats speedyapply MISSING.
    assert cluster.representative.platform == "greenhouse"
    assert cluster.representative.jd_quality == QualityBand.FULL


class _StaticGuestSource:
    """SimpleSource stub returning pre-built ``linkedin_guest`` postings.

    The guest source's own discovery/parsing is covered by its unit tests;
    this scenario only needs guest-tagged rows to land via the real
    ``ScanService`` -> store chain so the dedupe tiebreak runs on real rows.
    """

    def __init__(self, postings: list[JobPosting]) -> None:
        self._postings = postings

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        return list(self._postings)


def _guest_posting(
    *, title: str, company: str, canonical_id: str = "4009200"
) -> JobPosting:
    """Build one FULL-JD linkedin_guest posting for the twin scenario."""
    now = datetime.now(UTC)
    return JobPosting(
        platform="linkedin_guest",
        canonical_id=canonical_id,
        url=f"https://www.linkedin.com/jobs/view/{canonical_id}",
        title=title,
        company=company,
        location="New York, NY",
        discovered_at=now,
        jd_text=_FULL_JD,
        jd_quality=QualityBand.FULL,
        enriched_at=now,
        enrich_source="linkedin_guest",
        posted_at=datetime(2026, 5, 27, tzinfo=UTC),
    )


@respx.mock
async def test_cross_source_twin_source_priority_tiebreak(
    store: PostgresStore,
) -> None:
    """Two EQUAL-quality (FULL) twins on different platforms.

    Greenhouse and the LinkedIn guest source both produce a FULL-JD row for the
    same company+title. Quality ties, so rule 2 (source priority) decides: the
    greenhouse row (ATS-family rank 0) wins over linkedin_guest (rank 3).
    """
    await _seed_gh_company(store, "datadogats")
    respx.get(GH_JOBS_URL.format(slug="datadogats")).respond(
        200,
        json={
            "jobs": [
                _gh_job(
                    job_id=9200,
                    title="Platform Engineer",
                    company="Datadog",
                    content=f"<p>{_FULL_JD}</p>",
                    slug="datadogats",
                )
            ]
        },
    )
    # LinkedIn guest twin: same company+title, FULL JD.
    guest_source = _StaticGuestSource(
        [_guest_posting(title="Platform Engineer", company="Datadog")]
    )

    async with create_http_client() as client:
        service = ScanService(store, _logger())
        await service.run(
            [
                ("ats", _ats_source(client, store), {}),
                ("linkedin_guest", guest_source, {}),
            ]
        )

    jobs = await store.list_jobs()
    assert len(jobs) == _TWO_ROWS
    assert {j.platform for j in jobs} == {"greenhouse", "linkedin_guest"}

    clusters = cluster_twins(jobs)
    assert len(clusters) == _ONE_CLUSTER
    cluster = clusters[0]
    assert len(cluster.members) == _TWO_MEMBERS
    # Equal FULL quality on both members → source-priority tiebreak.
    assert all(m.jd_quality == QualityBand.FULL for m in cluster.members)
    assert cluster.representative.platform == "greenhouse"


# ===========================================================================
# Scenario 6: Idempotent re-scan
# ===========================================================================


@respx.mock
async def test_speedyapply_idempotent_rescan(store: PostgresStore) -> None:
    """Re-scanning the same SpeedyApply list UPSERTs; no duplicate rows."""
    gh_apply = "https://boards.greenhouse.io/acmeco/jobs/7100"
    readme = _readme(
        [("Acme Co", "Software Engineer Intern", "SF, CA", gh_apply, "2d")]
    )
    respx.get(_README_URL).respond(200, text=readme)
    respx.get(GH_JOB_URL.format(slug="acmeco", job_id="7100")).respond(
        200,
        json=_gh_job(
            job_id=7100,
            title="SWE Intern",
            company="Acme Co",
            content=f"<p>{_FULL_JD}</p>",
            slug="acmeco",
        ),
    )

    async with create_http_client() as client:
        service = ScanService(store, _logger())
        run1 = await service.run([("speedyapply", _speedyapply_source(client), {})])
        run2 = await service.run([("speedyapply", _speedyapply_source(client), {})])

    jobs = await store.list_jobs()
    assert len(jobs) == _ONE_ROW  # single row across both scans

    assert run1.jobs_inserted == _ONE_ROW
    assert run2.jobs_inserted == 0
    assert run2.jobs_updated == _ONE_ROW  # second run UPSERTs the same row


# ===========================================================================
# Scenario 7: Mixed success/failure containment (per-item)
# ===========================================================================


@respx.mock
async def test_speedyapply_per_item_failure_contained(store: PostgresStore) -> None:
    """One row's JD fetch fails (contained); both rows persist, run.errors == 0.

    The greenhouse single-job endpoint for the first row returns 500 — a
    per-ITEM failure that SpeedyApply contains (row kept with empty JD and the
    ``speedyapply-error`` label). It is NOT a source-level failure, so the run's
    error counter stays at 0 and the second (unrouted) row is unaffected.
    """
    failing_apply = "https://boards.greenhouse.io/acmeco/jobs/7200"
    unrouted_apply = "https://careers.globex.example.com/postings/swe-002"
    readme = _readme(
        [
            ("Acme Co", "Software Engineer Intern", "SF, CA", failing_apply, "2d"),
            ("Globex", "Backend Engineer Intern", "Remote", unrouted_apply, "4d"),
        ]
    )
    respx.get(_README_URL).respond(200, text=readme)
    # Routed greenhouse JD endpoint fails with a 500 (a per-item fetch failure).
    respx.get(GH_JOB_URL.format(slug="acmeco", job_id="7200")).respond(500)

    async with create_http_client() as client:
        service = ScanService(store, _logger())
        run = await service.run([("speedyapply", _speedyapply_source(client), {})])

    jobs = await store.list_jobs()
    # Per-item failure does NOT drop the row; both rows persist.
    assert len(jobs) == _TWO_ROWS

    failed_row = next(j for j in jobs if j.enrich_source == "speedyapply-error")
    assert failed_row.canonical_id == canonical_id_for(failing_apply)
    assert failed_row.jd_text is None
    assert failed_row.jd_quality == QualityBand.MISSING

    # The sibling unrouted row is unaffected.
    unrouted_row = next(j for j in jobs if j.enrich_source == "speedyapply-unrouted")
    assert unrouted_row.canonical_id == canonical_id_for(unrouted_apply)

    # Contained per-item failure → run-level error counter stays at 0.
    assert run.errors == 0
    assert run.jobs_inserted == _TWO_ROWS


# ===========================================================================
# Re-scan provenance: a no-JD re-scan must not relabel a preserved full JD
# ===========================================================================


async def test_rescan_failure_preserves_jd_provenance(store: PostgresStore) -> None:
    """A no-JD re-scan keeps the prior full JD AND its enrich_source.

    save_job keeps the higher-quality jd_text on conflict; enrich_source must
    stay aligned with it instead of adopting the failed re-scan's
    ``speedyapply-error`` label (Codex P2 review fix).
    """
    now = datetime(2026, 5, 20, tzinfo=UTC)
    cid = "sa-provenance-1"
    url = "https://boards.greenhouse.io/acmeco/jobs/9001"
    await store.save_job(
        JobPosting(
            platform="speedyapply",
            canonical_id=cid,
            url=url,
            title="Software Engineer Intern",
            company="Acme Co",
            location="SF, CA",
            discovered_at=now,
            jd_text=_FULL_JD,
            jd_quality=QualityBand.FULL,
            enriched_at=now,
            enrich_source="speedyapply-greenhouse",
        )
    )
    # Re-scan: a transient vendor failure yields no JD, MISSING, error label.
    await store.save_job(
        JobPosting(
            platform="speedyapply",
            canonical_id=cid,
            url=url,
            title="Software Engineer Intern",
            company="Acme Co",
            location="SF, CA",
            discovered_at=now,
            jd_text=None,
            jd_quality=QualityBand.MISSING,
            enriched_at=None,
            enrich_source="speedyapply-error",
        )
    )

    rows = [j for j in await store.list_jobs() if j.canonical_id == cid]
    assert len(rows) == _ONE_ROW
    assert rows[0].jd_text == _FULL_JD  # better JD preserved
    assert rows[0].jd_quality == QualityBand.FULL
    assert rows[0].enrich_source == "speedyapply-greenhouse"  # NOT clobbered
