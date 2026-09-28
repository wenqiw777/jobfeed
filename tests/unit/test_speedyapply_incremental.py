"""Cross-run JD reuse without network calls for complete stored bodies."""

from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from jobfeed.adapters.sources._speedyapply_routing import RouteResult
from jobfeed.adapters.sources.speedyapply import SpeedyApplySource
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.cli._scan_sources import build_scan_sources
from jobfeed.config import Settings, SourcesSpeedyApplyConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.ports.source import PartialSourceFetchError, StoredEnrichment
from jobfeed.services.job_page_extraction import JobPageExtractor


@pytest.mark.parametrize("quality", [QualityBand.GOOD, QualityBand.FULL])
async def test_complete_stored_jd_skips_native_and_browser_fetch(quality):
    old_time = datetime(2025, 1, 1, tzinfo=UTC)
    stored = StoredEnrichment(
        jd_text="Existing complete job description. " * 50,
        quality=quality,
        enriched_at=old_time,
        enrich_source="github_extension",
    )
    lookup = SimpleNamespace(get_enrichment=AsyncMock(return_value=stored))
    bridge = SimpleNamespace(run_scan=AsyncMock())
    async with httpx.AsyncClient() as client:
        source = SpeedyApplySource(
            client=client,
            config=SourcesSpeedyApplyConfig(),
            logger=MagicMock(),
            enrichment_lookup=lookup,
            bridge=bridge,
        )
        source._collect_rows = AsyncMock(return_value=[_row()])
        source._route = AsyncMock(side_effect=AssertionError("must reuse JD"))
        jobs = await source.fetch_jobs({})
    lookup.get_enrichment.assert_awaited_once_with(
        platform="speedyapply", canonical_id="existing-id"
    )
    bridge.run_scan.assert_not_awaited()
    assert jobs[0].jd_text == stored.jd_text
    assert jobs[0].jd_quality == quality
    assert jobs[0].enriched_at == old_time
    assert jobs[0].enrich_source == stored.enrich_source
    assert jobs[0].title == "Updated listing title"
    assert jobs[0].location == "New location"


async def test_lookup_failure_does_not_launch_native_or_browser_requests():
    lookup = SimpleNamespace(
        get_enrichment=AsyncMock(side_effect=RuntimeError("DB unavailable"))
    )
    bridge = SimpleNamespace(run_scan=AsyncMock())
    async with httpx.AsyncClient() as client:
        source = SpeedyApplySource(
            client=client,
            config=SourcesSpeedyApplyConfig(),
            logger=MagicMock(),
            enrichment_lookup=lookup,
            bridge=bridge,
        )
        source._collect_rows = AsyncMock(return_value=[_row()])
        source._route = AsyncMock()
        with pytest.raises(RuntimeError, match="DB unavailable"):
            await source.fetch_jobs({})
        source._route.assert_not_awaited()
        bridge.run_scan.assert_not_awaited()


@pytest.mark.parametrize(
    "stored",
    [
        None,
        StoredEnrichment(jd_text="short", quality=QualityBand.STUB, enriched_at=None),
        StoredEnrichment(
            jd_text="partial", quality=QualityBand.PARTIAL, enriched_at=None
        ),
        StoredEnrichment(jd_text="   ", quality=QualityBand.FULL, enriched_at=None),
    ],
)
async def test_new_or_incomplete_lookup_still_fetches(stored):
    probe = (
        AsyncMock(side_effect=stored)
        if isinstance(stored, Exception)
        else AsyncMock(return_value=stored)
    )
    async with httpx.AsyncClient() as client:
        source = SpeedyApplySource(
            client=client,
            config=SourcesSpeedyApplyConfig(),
            logger=MagicMock(),
            enrichment_lookup=SimpleNamespace(get_enrichment=probe),
        )
        source._collect_rows = AsyncMock(return_value=[_row()])
        source._route = AsyncMock(
            return_value=RouteResult(jd_text="New JD " * 200, enrich_source="native")
        )
        jobs = await source.fetch_jobs({})
    source._route.assert_awaited_once()
    assert jobs[0].jd_text == "New JD " * 200


def _row():
    return SimpleNamespace(
        canonical_id="existing-id",
        apply_url="https://example.com/job/1",
        title="Updated listing title",
        company="Example",
        location="New location",
        posted_at=None,
    )


async def test_permission_block_rechecks_extension_without_refetching_native_jd():
    stored = StoredEnrichment(
        jd_text="",
        quality=QualityBand.MISSING,
        enriched_at=None,
        enrich_error_code="missing_permission",
        enrich_error="Missing extension permission",
        enrich_retry_after=datetime.now(UTC) + timedelta(days=7),
    )
    bridge = SimpleNamespace(
        run_scan=AsyncMock(
            return_value=[
                {
                    "id": "existing-id",
                    "url": "https://example.com/job/1",
                    "description": "Complete responsibilities and qualifications. "
                    * 200,
                }
            ]
        )
    )
    async with httpx.AsyncClient() as client:
        source = SpeedyApplySource(
            client=client,
            config=SourcesSpeedyApplyConfig(),
            logger=MagicMock(),
            bridge=bridge,
            enrichment_lookup=SimpleNamespace(
                get_enrichment=AsyncMock(return_value=stored)
            ),
        )
        source._collect_rows = AsyncMock(return_value=[_row()])
        source._route = AsyncMock(side_effect=AssertionError("no native refetch"))
        jobs = await source.fetch_jobs({})
    bridge.run_scan.assert_awaited_once()
    assert jobs[0].enrich_error_code is None


async def test_url_aliases_share_one_native_and_browser_attempt_then_defer(tmp_path):
    store = SQLiteStore(tmp_path / "retry.sqlite")
    await store.connect()
    first, second = _row(), _row()
    first.apply_url = "https://hrt.com/job?gh_jid=8052122"
    second.apply_url = "https://boards.greenhouse.io/embed/job_app?token=8052122"
    second.canonical_id = "alias"
    bridge = SimpleNamespace(
        run_scan=AsyncMock(
            return_value=[
                {
                    "id": first.canonical_id,
                    "url": first.apply_url,
                    "source": "github-jd",
                    "error": "Job identity not found in page",
                }
            ]
        )
    )
    try:
        async with httpx.AsyncClient() as client:
            source = SpeedyApplySource(
                client=client,
                config=SourcesSpeedyApplyConfig(),
                logger=MagicMock(),
                enrichment_lookup=store,
                bridge=bridge,
            )
            source._collect_rows = AsyncMock(return_value=[first, second])
            source._route = AsyncMock(
                return_value=RouteResult(jd_text="", enrich_source="native")
            )
            with pytest.raises(PartialSourceFetchError) as caught:
                await source.fetch_jobs({})
            assert {job.canonical_id for job in caught.value.postings} == {
                first.canonical_id,
                second.canonical_id,
            }
            source._route.assert_awaited_once()
            assert len(bridge.run_scan.call_args.kwargs["targets"]) == 1
            for job in caught.value.postings:
                assert job.enrich_error_code == "identity_not_found"
                await store.save_job(job)
            source._route.reset_mock()
            bridge.run_scan.reset_mock()
            jobs = await source.fetch_jobs({})
            assert {job.canonical_id for job in jobs} == {
                first.canonical_id,
                second.canonical_id,
            }
            source._route.assert_not_awaited()
            bridge.run_scan.assert_not_awaited()
    finally:
        await store.close()


async def test_cross_source_complete_twin_and_cooldown_skip_all_network():
    for complete in (True, False):
        stored = StoredEnrichment(
            jd_text="Complete body" if complete else None,
            quality=QualityBand.FULL if complete else QualityBand.MISSING,
            enriched_at=None,
            platform="handshake",
            external_identity="greenhouse:8052122",
            enrich_retry_after=None
            if complete
            else datetime.now(UTC) + timedelta(days=7),
            enrich_error_code=None if complete else "identity_not_found",
        )
        lookup = SimpleNamespace(
            get_enrichment=AsyncMock(return_value=None),
            get_enrichment_by_identity=AsyncMock(return_value=stored),
        )
        bridge = SimpleNamespace(run_scan=AsyncMock())
        async with httpx.AsyncClient() as client:
            source = SpeedyApplySource(
                client=client,
                config=SourcesSpeedyApplyConfig(),
                logger=MagicMock(),
                enrichment_lookup=lookup,
                bridge=bridge,
            )
            row = _row()
            row.apply_url = "https://hrt.com/job?gh_jid=8052122"
            source._collect_rows = AsyncMock(return_value=[row])
            source._route = AsyncMock(side_effect=AssertionError("must skip"))
            jobs = await source.fetch_jobs({})
        source._route.assert_not_awaited()
        bridge.run_scan.assert_not_awaited()
        assert jobs[0].external_identity == "greenhouse:8052122"
        assert jobs[0].enrich_retry_after == stored.enrich_retry_after


async def test_runtime_wiring_reuses_sqlite_body_after_reconnect(tmp_path):
    store = SQLiteStore(tmp_path / "incremental.sqlite")
    old_time = datetime(2025, 1, 1, tzinfo=UTC)
    body = "Previously fetched engineering requirements. " * 50
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="speedyapply",
                canonical_id="existing-id",
                url=_row().apply_url,
                title="Old title",
                company="Example",
                location="Old location",
                discovered_at=old_time,
                jd_text=body,
                jd_quality=QualityBand.FULL,
                enriched_at=old_time,
                enrich_source="github_extension",
            )
        )
        await store.close()
        await store.connect()
        bridge = SimpleNamespace(run_scan=AsyncMock())
        app = {
            "settings": Settings.model_validate(
                {
                    "sources": {
                        "speedyapply": {
                            "enabled": True,
                            "search_urls": ["https://example.com/list.md"],
                        }
                    }
                }
            ),
            "store": store,
            "logger": MagicMock(),
            "jobright_bridge": bridge,
        }
        async with AsyncExitStack() as stack:
            sources = await build_scan_sources(app, "speedyapply", stack)
            source = sources[0][1]
            source._collect_rows = AsyncMock(return_value=[_row()])
            source._route = AsyncMock(side_effect=AssertionError("no refetch"))
            jobs = await source.fetch_jobs({})
            again = await store.save_job(jobs[0])
        assert again.job_id == saved.job_id
        persisted = await store.get_enrichment(
            platform="speedyapply", canonical_id="existing-id"
        )
        assert persisted.jd_text == body
        assert persisted.enriched_at == old_time
        assert jobs[0].title == "Updated listing title"
        bridge.run_scan.assert_not_awaited()
        source._route.assert_not_awaited()
    finally:
        await store.close()


async def test_new_extractor_retries_old_failure_once_then_honors_cooldown(tmp_path):

    store = SQLiteStore(tmp_path / "revision.sqlite")
    await store.connect()
    row = _row()
    await store.save_job(
        JobPosting(
            platform="speedyapply",
            canonical_id=row.canonical_id,
            url=row.apply_url,
            title=row.title,
            company=row.company,
            location=row.location,
            discovered_at=datetime.now(UTC),
            jd_quality=QualityBand.MISSING,
            enrich_error="No tab with id: 42.",
            enrich_error_code="parse_failed",
            enrich_retry_after=datetime.now(UTC) + timedelta(days=7),
        )
    )
    bridge = SimpleNamespace(
        run_scan=AsyncMock(
            return_value=[
                {
                    "id": row.canonical_id,
                    "url": row.apply_url,
                    "error": "Source page did not finish loading",
                    "error_code": "page_timeout",
                }
            ]
        )
    )
    try:
        async with httpx.AsyncClient() as client:
            source = SpeedyApplySource(
                client=client,
                config=SourcesSpeedyApplyConfig(),
                logger=MagicMock(),
                enrichment_lookup=store,
                bridge=bridge,
                page_extractor=JobPageExtractor(AsyncMock(), model="mock", store=store),
            )
            source._collect_rows = AsyncMock(return_value=[row])
            source._route = AsyncMock(
                side_effect=AssertionError("reuse native metadata")
            )
            with pytest.raises(PartialSourceFetchError) as caught:
                await source.fetch_jobs({})
            bridge.run_scan.assert_awaited_once()
            for job in caught.value.postings:
                await store.save_job(job)
            bridge.run_scan.reset_mock()
            await source.fetch_jobs({})
            bridge.run_scan.assert_not_awaited()
    finally:
        await store.close()
