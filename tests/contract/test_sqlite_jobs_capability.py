"""SQLite contracts for jobs persistence and ML-gate writes."""

from __future__ import annotations

import asyncio
import multiprocessing
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from jobfeed.adapters.store.sqlite_jobs_evaluations import SqliteJobsEvaluations
from jobfeed.adapters.store.sqlite_lifecycle import (
    SqliteLifecycle,
    SqliteLifecycleStateError,
)
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.models import MLGateResult, QualityBand
from tests.support.sqlite_jobs_evaluations import make_job, open_sqlite_store

_CANONICAL_TIMESTAMP_LENGTH = 27


async def test_rescan_preserves_first_discovered_time(tmp_path: Path) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "first-seen.db")
    try:
        first = datetime(2026, 8, 1, tzinfo=UTC)
        job = replace(make_job("repeat", discovered_at=first), posted_at=None)
        saved = await store.save_job(job)
        await store.save_job(
            replace(
                job, discovered_at=first + timedelta(days=40), title="Updated title"
            )
        )
        loaded = await store.get_job(saved.job_id)
        assert loaded.discovered_at == first
        assert loaded.title == "Updated title"
    finally:
        await lifecycle.close()


async def test_source_writer_assigns_one_stable_real_job(tmp_path: Path) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "real-job.db")
    try:
        posting = replace(
            make_job("same-source"),
            apply_url="https://qualcomm.eightfold.ai/careers?pid=446721162271",
        )
        saved = await store.save_job(posting)
        await store.save_job(posting)
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT real_job_id FROM jobs WHERE id=?", (int(saved.job_id),)
            )
            parent = (await cursor.fetchone())[0]
            await cursor.close()
            assert parent is not None
            cursor = await connection.execute(
                "SELECT apply_url FROM jobs WHERE id=?", (int(saved.job_id),)
            )
            assert (await cursor.fetchone())[0] == posting.apply_url
            await cursor.close()
            cursor = await connection.execute("SELECT COUNT(*) FROM real_jobs")
            assert (await cursor.fetchone())[0] == 1
            await cursor.close()
    finally:
        await lifecycle.close()


async def test_linkedin_unknown_cannot_erase_verified_company(tmp_path: Path) -> None:
    """A partial rescan keeps the known company but accepts a later real name."""
    lifecycle, store = await open_sqlite_store(tmp_path / "company.db")
    try:
        job = replace(make_job("123"), platform="linkedin", company="Capital One")
        saved = await store.save_job(job)
        await store.save_job(replace(job, company="Unknown"))
        assert (await store.get_job(saved.job_id)).company == "Capital One"
        await store.save_job(replace(job, company="Verified new name"))
        assert (await store.get_job(saved.job_id)).company == "Verified new name"
    finally:
        await lifecycle.close()


async def test_save_get_list_and_exact_exists_round_trip(tmp_path: Path) -> None:
    """Jobs round-trip canonical UTC and use a stable recency/ID order."""
    lifecycle, store = await open_sqlite_store(tmp_path / "jobs.db")
    try:
        offset_time = datetime(2026, 8, 12, 8, 0, tzinfo=timezone(timedelta(hours=-4)))
        first = await store.save_job(make_job("same", discovered_at=offset_time))
        second = await store.save_job(make_job("newer-id", discovered_at=offset_time))

        assert first.inserted and not first.updated
        assert await store.job_exists(platform="mock", canonical_id="same")
        assert not await store.job_exists(platform="MOCK", canonical_id="same")
        loaded = await store.get_job(first.job_id)
        assert loaded is not None
        assert loaded.discovered_at == datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        assert [job.id for job in await store.list_jobs()] == [
            second.job_id,
            first.job_id,
        ]
        assert await store.get_job("999999") is None
        with pytest.raises(ValueError):
            await store.get_job("not-an-id")
        with pytest.raises(ValueError, match="limit"):
            await store.list_jobs(-1)
    finally:
        await lifecycle.close()


async def test_quality_aware_upsert_preserves_or_resets_gate_inputs(
    tmp_path: Path,
) -> None:
    """Only the winning JD or a changed title invalidates persisted gate output."""
    lifecycle, store = await open_sqlite_store(tmp_path / "quality.db")
    try:
        inserted = await store.save_job(make_job("quality"))
        gate = MLGateResult(score=0.9, result="pass", version="v1")
        await store.save_ml_gate_result(inserted.job_id, gate)

        losing = make_job(
            "quality",
            jd_text="stub",
            jd_quality=QualityBand.STUB,
            enrich_error="transient",
        )
        replay = await store.save_job(losing)
        assert replay.job_id == inserted.job_id
        assert not replay.inserted and replay.updated
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT jd_text, jd_quality, enrich_source, ml_gate_result "
                "FROM jobs WHERE id=?",
                (int(inserted.job_id),),
            )
            assert await cursor.fetchone() == (
                "complete job description",
                "full",
                "fixture",
                "pass",
            )
            await cursor.close()

        await store.save_job(make_job("quality", title="Platform Engineer"))
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT ml_gate_score, ml_gate_result, ml_gate_fail_reason, "
                "ml_gate_at, ml_gate_version FROM jobs WHERE id=?",
                (int(inserted.job_id),),
            )
            assert await cursor.fetchone() == (None, None, None, None, None)
            await cursor.close()
    finally:
        await lifecycle.close()


async def test_save_job_reopens_with_jd_and_keeps_earliest_closure(
    tmp_path: Path,
) -> None:
    """A valid JD self-heals closure; no-JD rescans keep the first closure."""
    lifecycle, store = await open_sqlite_store(tmp_path / "closure.db")
    try:
        early = datetime(2026, 8, 10, tzinfo=UTC)
        late = datetime(2026, 8, 11, tzinfo=UTC)
        await store.save_job(
            make_job(
                "closed",
                jd_text=None,
                jd_quality=QualityBand.MISSING,
                closed_at=early,
                enrich_error="gone",
            )
        )
        result = await store.save_job(
            make_job(
                "closed",
                jd_text=None,
                jd_quality=QualityBand.MISSING,
                closed_at=late,
                enrich_error=None,
            )
        )
        loaded = await store.get_job(result.job_id)
        assert loaded is not None and loaded.closed_at == early
        assert loaded.enrich_error == "gone"

        await store.save_job(make_job("closed"))
        loaded = await store.get_job(result.job_id)
        assert loaded is not None
        assert loaded.closed_at is None and loaded.enrich_error is None
    finally:
        await lifecycle.close()


def _save_job_process(
    path: str,
    title: str,
    ready: Any,
    start: Any,
    outcomes: Any,
) -> None:
    async def run() -> None:
        lifecycle = SqliteLifecycle(Path(path), ensure_sqlite_schema)
        await lifecycle.open()
        try:
            ready.put(True)
            start.wait()
            result = await SqliteJobsEvaluations(lifecycle).save_job(
                make_job("raced", title=title)
            )
            outcomes.put((result.job_id, result.inserted, result.updated))
        finally:
            await lifecycle.close()

    asyncio.run(run())


def test_two_processes_report_one_truthful_natural_key_insert(
    tmp_path: Path,
) -> None:
    """Independent OS processes serialize a natural-key race."""
    path = tmp_path / "race.db"
    lifecycle, _store = asyncio.run(open_sqlite_store(path))
    asyncio.run(lifecycle.close())
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_save_job_process,
            args=(str(path), title, ready, start, outcomes),
        )
        for title in ("First", "Second")
    ]

    for process in processes:
        process.start()
    assert ready.get(timeout=10) is True
    assert ready.get(timeout=10) is True
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    rows = [outcomes.get(timeout=2), outcomes.get(timeout=2)]
    assert sorted((row[1], row[2]) for row in rows) == [
        (False, True),
        (True, False),
    ]
    assert rows[0][0] == rows[1][0]
    reopened, reopened_store = asyncio.run(open_sqlite_store(path))
    try:
        assert len(asyncio.run(reopened_store.list_jobs())) == 1
    finally:
        asyncio.run(reopened.close())


async def test_ml_gate_json_boolean_and_clock_are_canonical(tmp_path: Path) -> None:
    """Gate writes preserve types and canonical compact Unicode JSON bytes."""
    lifecycle, store = await open_sqlite_store(tmp_path / "gate.db")
    try:
        saved = await store.save_job(make_job("gate"))
        await store.save_ml_gate_result(
            saved.job_id,
            MLGateResult(
                score=0.75,
                result="fail",
                is_swe_role=True,
                clearance_required=False,
                domain_tags=["平台", "backend"],
                tech_required=[],
            ),
        )
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT is_swe_role, clearance_required, domain_tags, "
                "tech_required, ml_gate_at FROM jobs WHERE id=?",
                (int(saved.job_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        assert row[:4] == (1, 0, '["平台","backend"]', None)
        assert row[4].endswith("Z")
        assert len(row[4]) == _CANONICAL_TIMESTAMP_LENGTH
    finally:
        await lifecycle.close()


async def test_closed_lifecycle_errors_are_not_converted_to_empty_results(
    tmp_path: Path,
) -> None:
    """Capability methods propagate lifecycle state errors."""
    lifecycle, store = await open_sqlite_store(tmp_path / "closed.db")
    await lifecycle.close()
    with pytest.raises(SqliteLifecycleStateError):
        await store.list_jobs()
