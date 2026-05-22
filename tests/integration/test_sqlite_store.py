"""Integration tests for the SQLiteStore adapter."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import (
    JobPosting,
    PipelineRun,
    QualityBand,
    SaveJobResult,
    StageAResult,
    StageBResult,
)
from jobfeed.domain.scoring import parse_stage_b_response
from tests.support.factories import make_job as make_base_job

HIGH_STAGE_A_SCORE = 80
LOW_STAGE_A_SCORE = 40
STAGE_B_SCORE = 72
STAGE_A_COST = 0.10
STAGE_B_COST = 0.25
RUN_STAGE_A_COUNT = 3
RUN_STAGE_B_COUNT = 2


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[SQLiteStore]:
    """Create a connected temp-file SQLite store.

    Args:
        tmp_path: Temporary directory for the SQLite database.

    Yields:
        Connected SQLite store.
    """
    sqlite_store = SQLiteStore(tmp_path / "jobfeed.db")
    await sqlite_store.connect()
    try:
        yield sqlite_store
    finally:
        await sqlite_store.close()


class SlowSaveSQLiteStore(SQLiteStore):
    """SQLiteStore variant that exposes a save-in-progress synchronization point."""

    def __init__(self, db_path: Path) -> None:
        """Create a store that pauses briefly inside save_job transactions.

        Args:
            db_path: SQLite database path.
        """
        super().__init__(db_path)
        self.save_entered = asyncio.Event()

    async def _save_job_in_transaction(self, job: JobPosting) -> SaveJobResult:
        """Mark save entry before delegating to the real transaction body.

        Args:
            job: Job posting to persist.

        Returns:
            Upsert result from the production transaction body.
        """
        self.save_entered.set()
        await asyncio.sleep(0)
        return await super()._save_job_in_transaction(job)


def make_store_job(
    canonical_id: str = "mock-1",
    *,
    jd_text: str | None = "Detailed JD",
) -> JobPosting:
    """Create a valid job posting for store tests.

    Args:
        canonical_id: Source-specific natural identity.
        jd_text: Optional JD text to persist.

    Returns:
        Job posting with deterministic timestamps.
    """
    return make_base_job(
        canonical_id,
        jd_text=jd_text,
        jd_quality=QualityBand.GOOD if jd_text is not None else None,
    )


async def test_connect_is_idempotent(tmp_path: Path) -> None:
    """Repeated connect calls should reuse the existing SQLite connection.

    Args:
        tmp_path: Temporary directory for the SQLite database.
    """
    sqlite_store = SQLiteStore(tmp_path / "idempotent.db")
    await sqlite_store.connect()
    try:
        first_connection = sqlite_store._db

        await sqlite_store.connect()

        assert sqlite_store._db is first_connection
    finally:
        await sqlite_store.close()


async def test_close_waits_for_in_flight_save(tmp_path: Path) -> None:
    """close should not tear down the connection under an active write.

    Args:
        tmp_path: Temporary directory for the SQLite database.
    """
    db_path = tmp_path / "close-race.db"
    sqlite_store = SlowSaveSQLiteStore(db_path)
    await sqlite_store.connect()
    save_task = asyncio.create_task(sqlite_store.save_job(make_store_job("close-race")))
    await sqlite_store.save_entered.wait()
    close_task = asyncio.create_task(sqlite_store.close())

    saved, _ = await asyncio.gather(save_task, close_task)

    assert saved.inserted is True
    assert sqlite_store._db is None
    verifier = SQLiteStore(db_path)
    await verifier.connect()
    try:
        loaded = await verifier.get_job(saved.job_id)
    finally:
        await verifier.close()
    assert loaded is not None
    assert loaded.canonical_id == "close-race"


def make_stage_a(score: int = HIGH_STAGE_A_SCORE) -> StageAResult:
    """Create a Stage A result for store tests.

    Args:
        score: Stage A score to persist.

    Returns:
        Stage A result with deterministic metadata.
    """
    return StageAResult(
        score=score,
        one_line="Good fit",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="stage-a-prompt",
        resume_hash="resume-a",
        cost_usd=STAGE_A_COST,
    )


def make_stage_b_raw() -> str:
    """Create canonical Stage B JSON for persistence tests.

    Returns:
        Raw JSON matching the Stage B parser contract.
    """
    return json.dumps(
        {
            "block_a": {"verdict": "consider"},
            "block_b": {"summary": "Detailed summary"},
            "block_c": {
                "score_0_100": STAGE_B_SCORE,
                "strong_match": [
                    {"requirement": "Python", "evidence": "Built Python services."}
                ],
                "gaps": [
                    {
                        "requirement": "Kubernetes",
                        "severity": "minor",
                        "mitigation": "Discuss Docker experience.",
                    }
                ],
            },
            "block_e": {"hooks": ["Mention automation."]},
        }
    )


def make_stage_b() -> StageBResult:
    """Create a parsed Stage B result.

    Returns:
        Stage B domain result with raw blocks preserved.
    """
    return parse_stage_b_response(
        make_stage_b_raw(),
        model="mock/stage-b",
        prompt_hash="stage-b-prompt",
        resume_hash="resume-b",
        cost_usd=STAGE_B_COST,
    )


async def test_save_job_and_get_job_round_trip(store: SQLiteStore) -> None:
    """save_job and get_job should round-trip a posting."""
    saved = await store.save_job(make_store_job())

    loaded = await store.get_job(saved.job_id)

    assert saved.inserted is True
    assert saved.updated is False
    assert loaded is not None
    assert loaded.id == saved.job_id
    assert loaded.jd_quality is QualityBand.GOOD


async def test_save_job_upserts_and_preserves_enrichment(
    store: SQLiteStore,
) -> None:
    """Upserts should update mutable fields without erasing enriched JD data."""
    inserted = await store.save_job(make_store_job())
    incoming = make_store_job(jd_text=None)
    incoming.title = "Updated Backend Intern"

    updated = await store.save_job(incoming)
    jobs = await store.list_jobs()
    loaded = await store.get_job(inserted.job_id)

    assert updated.job_id == inserted.job_id
    assert updated.inserted is False
    assert updated.updated is True
    assert len(jobs) == 1
    assert loaded is not None
    assert loaded.title == "Updated Backend Intern"
    assert loaded.jd_text == "Detailed JD"
    assert loaded.jd_quality is QualityBand.GOOD
    assert loaded.posted_at is not None
    assert loaded.enriched_at is not None
    assert loaded.enrich_source == "mock"


async def test_save_job_concurrent_duplicate_uses_single_row(tmp_path: Path) -> None:
    """Concurrent inserts for one source identity should upsert deterministically.

    Args:
        tmp_path: Temporary directory for a shared SQLite database.
    """
    db_path = tmp_path / "race.db"
    first_store = SQLiteStore(db_path)
    second_store = SQLiteStore(db_path)
    await first_store.connect()
    await second_store.connect()
    try:
        first, second = await asyncio.gather(
            first_store.save_job(make_store_job("race")),
            second_store.save_job(make_store_job("race")),
        )
        jobs = await first_store.list_jobs()
    finally:
        await first_store.close()
        await second_store.close()

    assert first.job_id == second.job_id
    assert len(jobs) == 1
    assert {first.inserted, second.inserted} == {True, False}
    assert {first.updated, second.updated} == {True, False}


async def test_stage_a_completion_gates_stage_b_pending(
    store: SQLiteStore,
) -> None:
    """Stage A completion gates Stage B — threshold is service responsibility."""
    high = await store.save_job(make_store_job("high"))
    low = await store.save_job(make_store_job("low"))

    await store.save_stage_a(high.job_id, make_stage_a(HIGH_STAGE_A_SCORE))
    await store.save_stage_a(low.job_id, make_stage_a(LOW_STAGE_A_SCORE))

    pending_a = await store.load_pending_stage_a()
    pending_b = await store.load_pending_stage_b()

    assert pending_a == []
    ids = {job.canonical_id for job in pending_b}
    assert ids == {"high", "low"}


async def test_stage_errors_are_retryable(
    store: SQLiteStore,
) -> None:
    """Errored stages should remain in pending queues (retry semantics)."""
    stage_a_job = await store.save_job(make_store_job("stage-a-error"))
    stage_b_job = await store.save_job(make_store_job("stage-b-error"))
    await store.save_stage_a(stage_b_job.job_id, make_stage_a())

    await store.save_stage_a_error(stage_a_job.job_id, "bad stage a")
    await store.save_stage_b_error(stage_b_job.job_id, "bad stage b")

    assert _eval_status(store.db_path, stage_a_job.job_id, "stage_a_status") == "error"
    assert _eval_status(store.db_path, stage_b_job.job_id, "stage_b_status") == "error"
    pending_b = await store.load_pending_stage_b()
    pending_b_ids = {j.canonical_id for j in pending_b}
    assert "stage-b-error" in pending_b_ids


async def test_stage_b_persists_raw_blocks_and_separate_metadata(
    store: SQLiteStore,
) -> None:
    """Stage B persistence should preserve raw blocks and not overwrite Stage A."""
    saved = await store.save_job(make_store_job())
    await store.save_stage_a(saved.job_id, make_stage_a())
    await store.save_stage_b(saved.job_id, make_stage_b())

    row = _eval_row(store.db_path, saved.job_id)
    evaluations = await store.list_evaluated_jobs()

    assert json.loads(row["stage_b_fit_json"])["score_0_100"] == STAGE_B_SCORE
    assert row["stage_a_model"] == "mock/stage-a"
    assert row["stage_b_model"] == "mock/stage-b"
    assert row["stage_a_prompt_hash"] == "stage-a-prompt"
    assert row["stage_b_prompt_hash"] == "stage-b-prompt"
    assert evaluations[0].stage_a is not None
    assert evaluations[0].stage_b is not None
    assert evaluations[0].stage_a.cost_usd == STAGE_A_COST
    assert evaluations[0].stage_b.cost_usd == STAGE_B_COST
    assert evaluations[0].stage_b.raw_blocks == json.loads(make_stage_b_raw())


async def test_stage_b_mapping_rejects_missing_raw_block_fields(
    store: SQLiteStore,
) -> None:
    """Stage B row mapping should fail clearly on corrupt raw block JSON."""
    saved = await store.save_job(make_store_job("missing-raw-field"))
    await store.save_stage_a(saved.job_id, make_stage_a())
    await store.save_stage_b(saved.job_id, make_stage_b())
    _set_eval_column(
        store.db_path,
        saved.job_id,
        "stage_b_fit_json",
        json.dumps({"score_0_100": STAGE_B_SCORE, "gaps": []}),
    )

    with pytest.raises(ValueError, match="missing list column: strong_match"):
        await store.list_evaluated_jobs()


async def test_stage_b_mapping_rejects_invalid_gap_severity(
    store: SQLiteStore,
) -> None:
    """Stage B row mapping should validate severity values from storage."""
    saved = await store.save_job(make_store_job("bad-severity"))
    await store.save_stage_a(saved.job_id, make_stage_a())
    await store.save_stage_b(saved.job_id, make_stage_b())
    block_c = json.loads(make_stage_b_raw())["block_c"]
    assert isinstance(block_c, dict)
    gaps = block_c["gaps"]
    assert isinstance(gaps, list)
    gap = gaps[0]
    assert isinstance(gap, dict)
    gap["severity"] = "blocker"
    col = "stage_b_fit_json"
    _set_eval_column(store.db_path, saved.job_id, col, json.dumps(block_c))

    with pytest.raises(ValueError, match="invalid gap severity: blocker"):
        await store.list_evaluated_jobs()


async def test_list_evaluated_jobs_keeps_job_id_when_eval_id_differs(
    store: SQLiteStore,
) -> None:
    """Joined evaluation rows should not let evaluations.id override jobs.id."""
    first = await store.save_job(make_store_job("first"))
    second = await store.save_job(make_store_job("second"))
    await store.save_stage_a(second.job_id, make_stage_a())

    evaluations = await store.list_evaluated_jobs()

    assert first.job_id != second.job_id
    assert evaluations[0].job.id == second.job_id


async def test_pipeline_run_round_trips_stage_counts(store: SQLiteStore) -> None:
    """Pipeline run persistence should keep Stage A and Stage B counts separate."""
    run = PipelineRun(
        run_id="run-1",
        started_at=datetime(2026, 5, 21, 14, 0, tzinfo=UTC),
        source="evaluate",
        stage_a_scored=RUN_STAGE_A_COUNT,
        stage_b_scored=RUN_STAGE_B_COUNT,
        jobs_scored=RUN_STAGE_A_COUNT + RUN_STAGE_B_COUNT,
        finished_at=datetime(2026, 5, 21, 14, 5, tzinfo=UTC),
    )

    await store.record_pipeline_run(run)
    loaded = await store.get_pipeline_run("run-1")

    assert loaded is not None
    assert loaded.stage_a_scored == RUN_STAGE_A_COUNT
    assert loaded.stage_b_scored == RUN_STAGE_B_COUNT
    assert loaded.finished_at == run.finished_at


async def test_schema_rejects_missing_pipeline_run_source(
    store: SQLiteStore,
) -> None:
    """Pipeline runs should require the domain-mandated source field."""
    with (
        sqlite3.connect(store.db_path) as db,
        pytest.raises(sqlite3.IntegrityError),
    ):
        db.execute(
            "INSERT INTO pipeline_runs (run_id, started_at) VALUES (?, ?)",
            ("missing-source", datetime(2026, 5, 21, 14, 0, tzinfo=UTC).isoformat()),
        )


async def test_evaluations_job_id_is_unique(store: SQLiteStore) -> None:
    """The evaluations table should enforce one row per job."""
    saved = await store.save_job(make_store_job())

    async with aiosqlite.connect(store.db_path) as db:
        await db.execute("INSERT INTO evaluations (job_id) VALUES (?)", (saved.job_id,))
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO evaluations (job_id) VALUES (?)",
                (saved.job_id,),
            )


async def test_evaluations_foreign_key_requires_existing_job(
    store: SQLiteStore,
) -> None:
    """Evaluation rows should not persist without a matching job row."""
    with pytest.raises(sqlite3.IntegrityError):
        await store.save_stage_a_error("999", "missing job")


async def test_schema_rejects_missing_domain_required_job_fields(
    store: SQLiteStore,
) -> None:
    """DB schema should mirror domain-required job string fields."""
    with (
        sqlite3.connect(store.db_path) as db,
        pytest.raises(sqlite3.IntegrityError),
    ):
        db.execute(
            """
            INSERT INTO jobs (
                platform, canonical_id, title, company, location, discovered_at
            ) VALUES ('mock', 'bad', 'Title', 'Company', 'Remote', '2026-05-21')
            """
        )


async def test_schema_rejects_invalid_status_score_and_verdict(
    store: SQLiteStore,
) -> None:
    """DB CHECK constraints should reject invalid persisted enum and score data."""
    saved = await store.save_job(make_store_job("checks"))
    with sqlite3.connect(store.db_path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO evaluations (job_id, stage_a_score) VALUES (?, ?)",
                (saved.job_id, 150),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO evaluations (job_id, stage_a_status) VALUES (?, ?)",
                (saved.job_id, "done"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO evaluations (job_id, stage_b_verdict) VALUES (?, ?)",
                (saved.job_id, "maybe"),
            )


async def test_evaluations_track_created_and_updated_timestamps(
    store: SQLiteStore,
) -> None:
    """Evaluation rows should include timestamp columns for later auditing."""
    saved = await store.save_job(make_store_job("timestamps"))

    await store.save_stage_a(saved.job_id, make_stage_a())
    row = _eval_row(store.db_path, saved.job_id)

    assert isinstance(row["created_at"], str)
    assert isinstance(row["updated_at"], str)


def _eval_status(db_path: Path, job_id: str, column: str) -> str:
    row = _eval_row(db_path, job_id)
    value = row[column]
    assert isinstance(value, str)
    return value


def _eval_row(db_path: Path, job_id: str) -> sqlite3.Row:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM evaluations WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert row is not None
    return row


def _set_eval_column(db_path: Path, job_id: str, column: str, value: str) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            f"UPDATE evaluations SET {column} = ? WHERE job_id = ?",
            (value, job_id),
        )
