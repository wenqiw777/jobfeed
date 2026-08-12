"""SQLite contracts for jobs persistence and ML-gate writes."""

from __future__ import annotations

import asyncio
import multiprocessing
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
