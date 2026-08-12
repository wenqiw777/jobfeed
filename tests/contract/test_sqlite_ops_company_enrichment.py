"""SQLite company, enrichment, and source lookup contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobfeed.domain.models import CompanyRecord, MLGateResult, QualityBand
from tests.support.sqlite_jobs_evaluations import make_job
from tests.support.sqlite_ops import open_sqlite_ops

_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_UPDATED_JOB_COUNT = 9
_INITIAL_FAILURES = 2
_BUMPED_FAILURES = 3


async def test_company_upsert_filters_remove_and_failure_counters(
    tmp_path: Path,
) -> None:
    """Companies preserve nullable fields, exact slugs, ordering, and counters."""
    lifecycle, ops, _jobs = await open_sqlite_ops(tmp_path / "companies.db")
    try:
        await ops.upsert_company(
            CompanyRecord(
                slug="Zulu",
                ats_vendor="greenhouse",
                ats_override=True,
                last_verified_at=_NOW,
                notes="first",
                job_count_last_scan=3,
            )
        )
        await ops.upsert_company(CompanyRecord(slug="alpha", ats_vendor=None))
        await ops.upsert_company(
            CompanyRecord(
                slug="Zulu",
                ats_vendor=None,
                ats_override=False,
                notes=None,
                job_count_last_scan=9,
                consecutive_discover_failures=2,
            )
        )
        company = await ops.get_company("Zulu")
        assert company is not None
        assert company.ats_vendor == "greenhouse"
        assert company.last_verified_at == _NOW
        assert company.notes == "first"
        assert not company.ats_override
        assert company.job_count_last_scan == _UPDATED_JOB_COUNT
        assert company.consecutive_discover_failures == _INITIAL_FAILURES
        assert await ops.get_company("zulu") is None
        assert [row.slug for row in await ops.list_companies()] == ["Zulu", "alpha"]
        assert [row.slug for row in await ops.list_companies(vendor="greenhouse")] == [
            "Zulu"
        ]
        assert await ops.bump_discover_failure("Zulu") == _BUMPED_FAILURES
        assert await ops.bump_discover_failure("missing") == 0
        await ops.reset_discover_failures("Zulu")
        assert (await ops.get_company("Zulu")).consecutive_discover_failures == 0  # type: ignore[union-attr]
        assert await ops.mark_company_removed("Zulu")
        assert not await ops.mark_company_removed("Zulu")
        assert not await ops.mark_company_removed("missing")
        assert [row.slug for row in await ops.list_companies()] == ["alpha"]
        assert [row.slug for row in await ops.list_companies(include_removed=True)] == [
            "Zulu",
            "alpha",
        ]
    finally:
        await lifecycle.close()


async def test_enrichment_write_queue_and_source_lookup_contract(
    tmp_path: Path,
) -> None:
    """Enrichment resets stale state and source probes retain exact semantics."""
    lifecycle, ops, jobs = await open_sqlite_ops(tmp_path / "enrichment.db")
    try:
        saved = await jobs.save_job(
            make_job(
                "target",
                jd_text=None,
                jd_quality=QualityBand.MISSING,
                closed_at=_NOW,
                enrich_error="gone:404",
            )
        )
        newer = await jobs.save_job(
            make_job(
                "newer",
                discovered_at=_NOW + timedelta(seconds=1),
                jd_text=None,
                jd_quality=None,
            )
        )
        empty = await ops.get_enrichment(platform="mock", canonical_id="newer")
        assert empty is not None
        assert (
            empty.jd_text,
            empty.quality,
            empty.enriched_at,
            empty.enrich_source,
        ) == (
            None,
            None,
            None,
            None,
        )
        await jobs.save_ml_gate_result(saved.job_id, result=_gate_result())
        assert [
            row.job_id
            for row in await ops.list_unenriched_jobs(platform="mock", limit=10)
        ] == [newer.job_id]

        posted = _NOW - timedelta(days=2)
        await ops.record_enrichment(
            job_id=saved.job_id,
            jd_text="完整职位描述",
            jd_quality="full",
            enriched_at=_NOW,
            enrich_source="linkedin",
            jd_lang="zh",
            posted_at=posted,
        )
        enrichment = await ops.get_enrichment(platform="mock", canonical_id="target")
        assert enrichment is not None
        assert enrichment.jd_text == "完整职位描述"
        assert enrichment.quality is QualityBand.FULL
        assert enrichment.enriched_at == _NOW
        assert enrichment.enrich_source == "linkedin"
        assert await ops.get_closed_canonical_ids(platform="mock") == set()
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT closed_at,enrich_error,posted_at,ml_gate_result,jd_lang "
                "FROM jobs WHERE id=?",
                (int(saved.job_id),),
            )
            assert await cursor.fetchone() == (
                None,
                None,
                "2026-08-10T12:00:00.000000Z",
                None,
                "zh",
            )
            await cursor.close()

        await ops.mark_job_closed(job_id=saved.job_id, closed_at=_NOW, reason="gone")
        await ops.mark_job_closed(
            job_id=saved.job_id,
            closed_at=_NOW + timedelta(hours=1),
        )
        assert await ops.get_closed_canonical_ids(platform="mock") == {"target"}
        loaded = await jobs.get_job(saved.job_id)
        assert loaded is not None
        assert loaded.closed_at == _NOW + timedelta(hours=1)
        assert loaded.enrich_error == "gone"
        with pytest.raises(ValueError):
            await ops.record_enrichment(
                job_id="bad",
                jd_text="x",
                jd_quality="stub",
                enriched_at=_NOW,
                enrich_source="test",
            )
        async with lifecycle.connection() as connection:
            await connection.execute("PRAGMA ignore_check_constraints=ON")
            await connection.execute(
                "UPDATE jobs SET jd_quality='invalid' WHERE id=?",
                (int(newer.job_id),),
            )
        with pytest.raises(ValueError, match="invalid"):
            await ops.get_enrichment(platform="mock", canonical_id="newer")
    finally:
        await lifecycle.close()


async def test_manual_paste_and_stale_closure_lookup(tmp_path: Path) -> None:
    """Paste wraps canonical enrichment and stale backfill remains refetchable."""
    lifecycle, ops, jobs = await open_sqlite_ops(tmp_path / "paste.db")
    try:
        saved = await jobs.save_job(
            make_job("paste", jd_text=None, jd_quality=QualityBand.MISSING)
        )
        assert (
            await ops.enrich_paste(
                platform="mock", canonical_id="paste", jd_text="x" * 1200
            )
            == saved.job_id
        )
        enrichment = await ops.get_enrichment(platform="mock", canonical_id="paste")
        assert enrichment is not None and enrichment.quality is QualityBand.FULL
        assert enrichment.enrich_source == "manual-paste"
        with pytest.raises(ValueError, match="job not found: mock/missing"):
            await ops.enrich_paste(
                platform="mock", canonical_id="missing", jd_text="text"
            )

        await ops.mark_job_closed(
            job_id=saved.job_id,
            closed_at=_NOW,
            reason="backfill:stale-no-jd",
        )
        assert await ops.get_closed_canonical_ids(platform="mock") == set()
    finally:
        await lifecycle.close()


def _gate_result() -> MLGateResult:
    return MLGateResult(score=0.8, result="pass", version="v1")
