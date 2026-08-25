"""SQLite v2 contracts for the unified evaluator result store."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from jobfeed.adapters.store import sqlite_schema
from jobfeed.adapters.store._sqlite_schema_metadata import (
    SQLITE_SCHEMA_VERSION,
    schema_v1_ddl_statements,
)
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema
from jobfeed.domain.errors import RunLeaseLostError
from tests.support.sqlite_jobs_evaluations import make_job

_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
_VERSION = "unified-v1"
_V1_VERSION = 1
_LEGACY_SCORE = 77
_UPDATED_SCORE = 92
_CONCURRENT_JOB_COUNT = 6
_CLAIM_ONE = "run-one:1"
_CLAIM_TWO = "run-two:2"
_EXPECTED_COLUMNS = (
    "job_id",
    "status",
    "eligibility_status",
    "match_tier",
    "match_score",
    "ats_visibility_score",
    "result_json",
    "evaluator_version",
    "model",
    "prompt_hash",
    "resume_hash",
    "cost_usd",
    "evaluated_at",
    "updated_at",
    "error",
    "error_count",
    "claim_token",
    "claim_started_at",
)


async def _scalar(connection: aiosqlite.Connection, sql: str) -> object:
    cursor = await connection.execute(sql)
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    return row[0]


async def _create_v1(connection: aiosqlite.Connection) -> None:
    for statement in schema_v1_ddl_statements():
        await connection.execute(statement)
    await connection.execute(
        "INSERT INTO run_leases(kind,generation) VALUES('scan',0),('evaluate',0)"
    )
    await connection.execute("PRAGMA user_version=1")
    await connection.commit()


async def _columns(connection: aiosqlite.Connection, table: str) -> tuple[str, ...]:
    cursor = await connection.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    await cursor.close()
    return tuple(str(row[1]) for row in rows)


def _result(*, score: int = 84, version: str = _VERSION) -> SimpleNamespace:
    return SimpleNamespace(
        eligibility_status="pass",
        match_tier="strong_match",
        match_score=score,
        ats_visibility_score=91,
        result_json={"score_breakdown": {"qualification": score}},
        evaluator_version=version,
        model="gpt-test",
        prompt_hash="prompt-hash",
        resume_hash="resume-hash",
        cost_usd=0.125,
    )


@pytest.mark.asyncio
async def test_empty_database_creates_exact_v2_schema() -> None:
    """A new database lands directly on exact v2 with the independent table."""
    async with aiosqlite.connect(":memory:") as connection:
        await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == SQLITE_SCHEMA_VERSION
        assert await _columns(connection, "evaluation_results") == _EXPECTED_COLUMNS
        assert (
            await _scalar(
                connection,
                "SELECT COUNT(*) FROM sqlite_schema "
                "WHERE type='table' AND name='evaluations'",
            )
            == 1
        )
        await connection.execute(
            "INSERT INTO llm_usage(model,input_tokens,output_tokens,stage) "
            "VALUES('gpt-test',1,2,'evaluation')"
        )


@pytest.mark.asyncio
async def test_v1_migrates_atomically_and_preserves_legacy_evaluations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v2 migration is additive, exact, and rolls back as one transaction."""
    async with aiosqlite.connect(":memory:") as connection:
        await _create_v1(connection)
        await connection.execute(
            """INSERT INTO jobs(platform,canonical_id,url,title,company,location,
                   discovered_at) VALUES('test','legacy','https://example.test',
                   'Engineer','Example','Remote','2026-08-25T00:00:00.000000Z')"""
        )
        job_id = await _scalar(connection, "SELECT id FROM jobs")
        await connection.execute(
            """INSERT INTO evaluations(job_id,stage_a_status,stage_a_score)
               VALUES(?, 'completed', ?)""",
            (job_id, _LEGACY_SCORE),
        )
        await connection.commit()

        original = sqlite_schema._execute_schema_statement

        async def fail_v2_ddl(conn: aiosqlite.Connection, sql: str) -> None:
            if "evaluation_results" in sql:
                raise RuntimeError("injected v2 migration failure")
            await original(conn, sql)

        monkeypatch.setattr(sqlite_schema, "_execute_schema_statement", fail_v2_ddl)
        with pytest.raises(RuntimeError, match="injected v2 migration failure"):
            await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == _V1_VERSION
        assert await _columns(connection, "evaluation_results") == ()
        assert (
            await _scalar(connection, "SELECT stage_a_score FROM evaluations")
            == _LEGACY_SCORE
        )

        monkeypatch.setattr(sqlite_schema, "_execute_schema_statement", original)
        await ensure_sqlite_schema(connection)

        assert await _scalar(connection, "PRAGMA user_version") == SQLITE_SCHEMA_VERSION
        assert await _columns(connection, "evaluation_results") == _EXPECTED_COLUMNS
        assert (
            await _scalar(connection, "SELECT stage_a_score FROM evaluations")
            == _LEGACY_SCORE
        )


@pytest.mark.asyncio
async def test_save_upserts_and_reads_one_current_result(tmp_path: Path) -> None:
    """Completion is one atomic upsert and read returns decoded current data."""
    path = tmp_path / "jobfeed.db"
    store = SQLiteStore(path, clock=lambda: _NOW)
    await store.connect()
    try:
        job_id = (await store.save_job(make_job("save"))).job_id

        await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_ONE,
            corpus="unrated",
            limit=1,
        )
        await store.save_evaluation(job_id, _result(), _CLAIM_ONE)
        await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_TWO,
            corpus="all",
            limit=1,
        )
        await store.save_evaluation(job_id, _result(score=_UPDATED_SCORE), _CLAIM_TWO)
        row = await store.get_current_evaluation(job_id)

        assert row is not None
        assert row["status"] == "completed"
        assert row["match_score"] == _UPDATED_SCORE
        assert row["result_json"] == {
            "score_breakdown": {"qualification": _UPDATED_SCORE}
        }
        assert row["evaluator_version"] == _VERSION
        async with aiosqlite.connect(path) as connection:
            assert (
                await _scalar(connection, "SELECT COUNT(*) FROM evaluation_results")
                == 1
            )
            cursor = await connection.execute(
                "SELECT status FROM job_status WHERE job_id=?", (int(job_id),)
            )
            assert await cursor.fetchone() == ("scored",)
            await cursor.close()
            cursor = await connection.execute(
                "SELECT from_status,to_status,reason FROM job_status_history "
                "WHERE job_id=? ORDER BY id",
                (int(job_id),),
            )
            assert await cursor.fetchall() == [
                (None, "new", None),
                ("new", "scored", "auto_scored"),
            ]
            await cursor.close()
    finally:
        await store.close()

    reopened = SQLiteStore(path, clock=lambda: _NOW)
    await reopened.connect()
    try:
        async with aiosqlite.connect(path) as connection:
            cursor = await connection.execute(
                "SELECT status FROM job_status WHERE job_id=?", (int(job_id),)
            )
            assert await cursor.fetchone() == ("scored",)
            await cursor.close()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_claim_uses_independent_versioned_state_and_corpus(
    tmp_path: Path,
) -> None:
    """Legacy-only and stale-version jobs claim; current completions do not."""
    store = SQLiteStore(tmp_path / "jobfeed.db", clock=lambda: _NOW)
    await store.connect()
    try:
        ids: dict[str, str] = {}
        for offset, name in enumerate(
            ("legacy-only", "empty", "old-version", "current", "failed")
        ):
            ids[name] = (
                await store.save_job(
                    make_job(name, discovered_at=_NOW - timedelta(minutes=offset))
                )
            ).job_id
        await store.save_stage_a(
            ids["legacy-only"],
            SimpleNamespace(
                score=70,
                one_line="legacy",
                timing_eligible="eligible",
                model="old",
                prompt_hash="old-prompt",
                resume_hash="old-resume",
                cost_usd=0.0,
            ),
        )
        await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_ONE,
            corpus="all",
            limit=10,
        )
        await store.save_evaluation(
            ids["old-version"], _result(version="old-v0"), _CLAIM_ONE
        )
        await store.save_evaluation(ids["current"], _result(), _CLAIM_ONE)
        await store.save_evaluation_error(ids["failed"], "boom", _VERSION, _CLAIM_ONE)
        await store.release_evaluation_claim(ids["legacy-only"], _VERSION, _CLAIM_ONE)
        await store.release_evaluation_claim(ids["empty"], _VERSION, _CLAIM_ONE)

        preview = await store.preview_pending_evaluations(
            evaluator_version=_VERSION,
            corpus="unrated",
            limit=10,
            max_days=30,
        )
        assert [job.canonical_id for job in preview] == [
            "legacy-only",
            "empty",
            "old-version",
            "failed",
        ]
        failed_before_claim = await store.get_current_evaluation(ids["failed"])
        assert failed_before_claim is not None
        assert failed_before_claim["status"] == "error"

        unrated = await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_TWO,
            corpus="unrated",
            limit=10,
            max_days=30,
        )
        assert [job.canonical_id for job in unrated] == [
            "legacy-only",
            "empty",
            "old-version",
            "failed",
        ]
        assert "current" not in {job.canonical_id for job in unrated}
        for job in unrated:
            assert job.id is not None
            await store.release_evaluation_claim(job.id, _VERSION, _CLAIM_TWO)

        claimed = await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token="run-all:3",
            corpus="all",
            limit=10,
            max_days=30,
        )
        assert [job.canonical_id for job in claimed] == [
            "legacy-only",
            "empty",
            "old-version",
            "current",
            "failed",
        ]
        for job in claimed:
            assert job.id is not None
            await store.release_evaluation_claim(job.id, _VERSION, "run-all:3")
        failed = await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token="run-failed:4",
            corpus="failed",
            limit=10,
            max_days=None,
        )
        assert [job.canonical_id for job in failed] == ["failed"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unrated_reclaims_only_stale_in_progress_rows(tmp_path: Path) -> None:
    """Fresh claims stay protected while interrupted claims become pending."""
    path = tmp_path / "jobfeed.db"
    store = SQLiteStore(path, clock=lambda: _NOW)
    await store.connect()
    try:
        for name in ("stale", "fresh"):
            await store.save_job(make_job(name))
        initial = await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_ONE,
            corpus="unrated",
            limit=10,
            max_days=None,
        )
        assert {job.canonical_id for job in initial} == {"stale", "fresh"}
        async with aiosqlite.connect(path) as connection:
            await connection.execute(
                "UPDATE evaluation_results SET claim_started_at=? WHERE job_id=("
                "SELECT id FROM jobs WHERE canonical_id='stale')",
                ((_NOW - timedelta(hours=2)).isoformat(),),
            )
            await connection.commit()

        preview = await store.preview_pending_evaluations(
            evaluator_version=_VERSION,
            corpus="unrated",
            limit=10,
            max_days=None,
        )
        assert [job.canonical_id for job in preview] == ["stale"]
        reclaimed = await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_TWO,
            corpus="unrated",
            limit=10,
            max_days=None,
        )
        assert [job.canonical_id for job in reclaimed] == ["stale"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_claims_are_disjoint(tmp_path: Path) -> None:
    """BEGIN IMMEDIATE makes two claimant batches disjoint and exhaustive."""
    path = tmp_path / "jobfeed.db"
    first = SQLiteStore(path, clock=lambda: _NOW)
    second = SQLiteStore(path, clock=lambda: _NOW)
    await first.connect()
    await second.connect()
    try:
        for index in range(_CONCURRENT_JOB_COUNT):
            await first.save_job(make_job(f"job-{index}"))

        left, right = await asyncio.gather(
            first.claim_pending_evaluations(
                evaluator_version=_VERSION,
                claim_token=_CLAIM_ONE,
                corpus="all",
                limit=3,
                max_days=None,
            ),
            second.claim_pending_evaluations(
                evaluator_version=_VERSION,
                claim_token=_CLAIM_TWO,
                corpus="all",
                limit=3,
                max_days=None,
            ),
        )

        left_ids = {job.id for job in left}
        right_ids = {job.id for job in right}
        assert left_ids.isdisjoint(right_ids)
        assert len(left_ids | right_ids) == _CONCURRENT_JOB_COUNT
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_failed_or_released_re_evaluation_preserves_completed_result(
    tmp_path: Path,
) -> None:
    """A failed replacement attempt must not erase the last good evaluation."""
    store = SQLiteStore(tmp_path / "jobfeed.db", clock=lambda: _NOW)
    await store.connect()
    try:
        job_id = (await store.save_job(make_job("preserve"))).job_id
        await store.claim_pending_evaluations(
            evaluator_version="old-v0",
            claim_token=_CLAIM_ONE,
            corpus="unrated",
            limit=1,
        )
        await store.save_evaluation(
            job_id, _result(score=84, version="old-v0"), _CLAIM_ONE
        )

        await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_TWO,
            corpus="unrated",
            limit=1,
        )
        during = await store.get_current_evaluation(job_id)
        assert during is not None
        assert (
            during["status"],
            during["match_score"],
            during["evaluator_version"],
        ) == (
            "completed",
            84,
            "old-v0",
        )

        await store.save_evaluation_error(
            job_id,
            "replacement failed",
            _VERSION,
            _CLAIM_TWO,
        )
        after_error = await store.get_current_evaluation(job_id)
        assert after_error is not None
        assert (
            after_error["status"],
            after_error["match_score"],
            after_error["evaluator_version"],
        ) == ("completed", 84, "old-v0")

        await store.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token="run-three:3",
            corpus="unrated",
            limit=1,
        )
        await store.release_evaluation_claim(job_id, _VERSION, "run-three:3")
        after_release = await store.get_current_evaluation(job_id)
        assert after_release is not None
        assert (after_release["match_score"], after_release["evaluator_version"]) == (
            84,
            "old-v0",
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_or_error_newer_result(
    tmp_path: Path,
) -> None:
    """Every terminal write is fenced by the exact claim token."""
    path = tmp_path / "jobfeed.db"
    first = SQLiteStore(path, clock=lambda: _NOW)
    second = SQLiteStore(path, clock=lambda: _NOW + timedelta(hours=2))
    await first.connect()
    await second.connect()
    try:
        job_id = (await first.save_job(make_job("fenced"))).job_id
        await first.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_ONE,
            corpus="unrated",
            limit=1,
        )
        reclaimed = await second.claim_pending_evaluations(
            evaluator_version=_VERSION,
            claim_token=_CLAIM_TWO,
            corpus="unrated",
            limit=1,
        )
        assert [job.id for job in reclaimed] == [job_id]
        await second.save_evaluation(job_id, _result(score=_UPDATED_SCORE), _CLAIM_TWO)

        with pytest.raises(RunLeaseLostError):
            await first.save_evaluation(job_id, _result(score=10), _CLAIM_ONE)
        with pytest.raises(RunLeaseLostError):
            await first.save_evaluation_error(
                job_id,
                "late old worker",
                _VERSION,
                _CLAIM_ONE,
            )

        row = await first.get_current_evaluation(job_id)
        assert row is not None
        assert (row["status"], row["match_score"]) == ("completed", _UPDATED_SCORE)
    finally:
        await first.close()
        await second.close()
