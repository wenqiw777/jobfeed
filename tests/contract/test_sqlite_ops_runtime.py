"""SQLite state, cost, usage, maintenance, health, and timing contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store import _sqlite_ops_cost, _sqlite_ops_health
from jobfeed.domain.models import LLMUsage, QualityBand
from jobfeed.domain.models_perf import StepTiming
from tests.support.sqlite_jobs_evaluations import make_job, stage_a
from tests.support.sqlite_ops import open_sqlite_ops

_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_CANONICAL_TIMESTAMP_LENGTH = 27


async def test_state_and_cost_accumulate_with_canonical_method_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State is exact and daily costs atomically add calls/spend at one time."""
    lifecycle, ops, _jobs = await open_sqlite_ops(tmp_path / "state-cost.db")
    monkeypatch.setattr(_sqlite_ops_cost, "_now", lambda: _NOW)
    try:
        assert await ops.get_state("cutoff") is None
        await ops.set_state("cutoff", "")
        assert await ops.get_state("cutoff") == ""
        await ops.set_state("cutoff", "next")
        assert await ops.get_state("cutoff") == "next"

        assert await ops.get_cost("2026-08-12") is None
        await ops.record_cost(day="2026-08-12", spent_usd=1.25, calls=1)
        await ops.record_cost(day="2026-08-12", spent_usd=0.75, calls=0)
        cost = await ops.get_cost("2026-08-12")
        assert cost is not None
        assert (cost.day, cost.spent_usd, cost.calls, cost.last_updated) == (
            "2026-08-12",
            2.0,
            1,
            _NOW,
        )
    finally:
        await lifecycle.close()


async def test_usage_with_cost_is_atomic_and_does_not_add_attempt_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paid-call usage and ledger spend commit together with calls unchanged."""
    lifecycle, ops, jobs = await open_sqlite_ops(tmp_path / "usage.db")
    saved = await jobs.save_job(make_job("usage"))
    usage = LLMUsage(
        model="model",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.2,
        cached=True,
        latency_ms=123,
        timestamp=_NOW,
        job_id=saved.job_id,
        stage="a",
        run_id="run-1",
    )
    monkeypatch.setattr(_sqlite_ops_cost, "_now", lambda: _NOW)
    try:
        await ops.record_cost(day="2026-08-12", spent_usd=0.0, calls=1)
        await ops.record_llm_usage_with_cost(
            day="2026-08-12", spent_usd=0.2, usage=usage
        )
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT model,input_tokens,output_tokens,cost_usd,cached,"
                "latency_ms,timestamp,job_id,stage,run_id FROM llm_usage"
            )
            assert await cursor.fetchone() == (
                "model",
                10,
                5,
                0.2,
                1,
                123,
                "2026-08-12T12:00:00.000000Z",
                int(saved.job_id),
                "a",
                "run-1",
            )
            await cursor.close()
        cost = await ops.get_cost("2026-08-12")
        assert cost is not None and (cost.spent_usd, cost.calls) == (0.2, 1)

        missing_job = LLMUsage(
            model="missing-job",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.1,
            cached=False,
            latency_ms=1,
            timestamp=_NOW,
            job_id="999999",
            stage="b",
        )
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await ops.record_llm_usage_with_cost(
                day="2026-08-13", spent_usd=0.1, usage=missing_job
            )
        assert await ops.get_cost("2026-08-13") is None

        async def reject_cost(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected ledger failure")

        monkeypatch.setattr(_sqlite_ops_cost, "_record_cost", reject_cost)
        with pytest.raises(RuntimeError, match="injected ledger"):
            await ops.record_llm_usage_with_cost(
                day="2026-08-14",
                spent_usd=0.3,
                usage=LLMUsage(
                    model="rollback",
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=0.3,
                    cached=False,
                    latency_ms=1,
                    timestamp=_NOW,
                ),
            )
        async with lifecycle.connection() as connection:
            cursor = await connection.execute("SELECT COUNT(*) FROM llm_usage")
            assert await cursor.fetchone() == (1,)
            await cursor.close()
        assert await ops.get_cost("2026-08-14") is None
    finally:
        await lifecycle.close()


async def test_attention_categories_are_independently_capped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health report keeps three independent unordered category limits."""
    lifecycle, ops, jobs = await open_sqlite_ops(tmp_path / "attention.db")
    monkeypatch.setattr(_sqlite_ops_health, "_now", lambda: _NOW)
    try:
        error_job = await jobs.save_job(
            make_job("error", discovered_at=_NOW, enrich_error="fetch failed")
        )
        low_job = await jobs.save_job(
            make_job(
                "low",
                discovered_at=_NOW,
                jd_text="stub",
                jd_quality=QualityBand.STUB,
            )
        )
        stuck_job = await jobs.save_job(
            make_job("stuck", discovered_at=_NOW - timedelta(days=30))
        )
        await jobs.save_stage_a(low_job.job_id, stage_a())
        for _ in range(3):
            await jobs.save_stage_a_error(stuck_job.job_id, "terminal")

        report = await ops.needs_attention(days=7, max_per_category=1)
        assert {row.job_id for row in report.enrich_errors} == {error_job.job_id}
        assert {row.job_id for row in report.low_quality_scored} == {low_job.job_id}
        assert {row.job_id for row in report.stuck_scoring} == {stuck_job.job_id}
    finally:
        await lifecycle.close()


async def test_stale_maintenance_dry_run_write_and_repeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale closure uses strict cutoff and dry-run/write parity."""
    lifecycle, ops, jobs = await open_sqlite_ops(tmp_path / "stale.db")
    monkeypatch.setattr(_sqlite_ops_health, "_now", lambda: _NOW)
    try:
        stale = await jobs.save_job(
            make_job(
                "stale",
                discovered_at=_NOW - timedelta(days=8),
                jd_text=None,
                jd_quality=QualityBand.MISSING,
            )
        )
        await jobs.save_job(
            make_job(
                "boundary",
                discovered_at=_NOW - timedelta(days=7),
                jd_text=None,
                jd_quality=QualityBand.MISSING,
            )
        )
        with pytest.raises(ValueError, match=">= 1"):
            await ops.mark_stale_jobs_closed(older_than_days=0, dry_run=True)
        assert await ops.mark_stale_jobs_closed(older_than_days=7, dry_run=True) == 1
        assert (await jobs.get_job(stale.job_id)).closed_at is None  # type: ignore[union-attr]
        assert await ops.mark_stale_jobs_closed(older_than_days=7, dry_run=False) == 1
        loaded = await jobs.get_job(stale.job_id)
        assert loaded is not None and loaded.closed_at == _NOW
        assert loaded.enrich_error == "backfill:stale-no-jd"
        assert await ops.get_closed_canonical_ids(platform="mock") == set()
        assert await ops.mark_stale_jobs_closed(older_than_days=7, dry_run=False) == 0
    finally:
        await lifecycle.close()


async def test_record_step_timing_uses_db_clock_and_fk(tmp_path: Path) -> None:
    """Single timing ignores input created_at and propagates missing-run FK."""
    lifecycle, ops, _jobs = await open_sqlite_ops(tmp_path / "timing.db")
    try:
        async with lifecycle.connection() as connection:
            await connection.execute(
                "INSERT INTO pipeline_runs(run_id,started_at,source,status) "
                "VALUES (?,?,?,?)",
                ("run-1", "2026-08-12T12:00:00.000000Z", "scan", "completed"),
            )
        await ops.record_step_timing(
            StepTiming(
                run_id="run-1",
                step_type="source",
                step_name="fetch",
                elapsed_ms=12.5,
                is_error=True,
                created_at=datetime(2000, 1, 1, tzinfo=UTC),
            )
        )
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT step_type,step_name,elapsed_ms,is_error,created_at "
                "FROM step_timings"
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None and row[:4] == ("source", "fetch", 12.5, 1)
        assert len(row[4]) == _CANONICAL_TIMESTAMP_LENGTH and row[4].endswith("Z")
        assert not row[4].startswith("2000-")
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await ops.record_step_timing(
                StepTiming(
                    run_id="missing",
                    step_type="source",
                    step_name="fetch",
                    elapsed_ms=1,
                )
            )
    finally:
        await lifecycle.close()
