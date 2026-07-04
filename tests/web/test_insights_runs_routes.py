"""Insights, attention, and runs routes tests (PG-backed).

Pins the Task 5 acceptance criteria over real HTTP + PostgreSQL:

* Overview math against a seeded fixture: all-time totals, the verdict
  distribution (incl. the derived below_threshold bucket), the status
  distribution, and the exact daily UTC series — with one row seeded exactly
  on a UTC day boundary (00:00:00Z) to prove UTC bucketing, and one row
  outside the window to prove totals are all-time while the series is
  windowed. The series contains only days having data (the pinned choice).
* Attention payload mirrors the digest footer's six buckets (three workflow
  + three pipeline-health) for one fixture exercising all of them, and
  matches the same store calls' data.
* Runs list is newest-first (started_at DESC, run_id DESC tiebreak) with
  round-tripped counters, correct pagination totals, and a 404 in the shared
  error shape for an unknown run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import asyncpg
import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    ApplicationRecord,
    FitAnalysis,
    MLGateResult,
    PipelineRun,
    QualityBand,
    StageAResult,
    StageBResult,
    TransitionRequest,
    Verdict,
)
from jobfeed.domain.scoring import MAX_STAGE_RETRIES
from jobfeed.web.app import create_web_app
from tests.support.factories import make_job
from tests.web.test_app_skeleton import open_client

pytestmark = pytest.mark.postgres

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_VALIDATION_ERROR = 422

_WINDOW_DAYS = 30
_STAGE_A_SCORE = 80
_STAGE_B_FIT_SCORE = 77
_STUCK_ERROR_COUNT = MAX_STAGE_RETRIES  # past the retry cap -> stuck_scoring


def _write_config(tmp_path: Path, dsn: str) -> Path:
    """Write a TOML config pointing at the test database.

    Args:
        tmp_path: Temporary directory owned by pytest.
        dsn: PostgreSQL DSN.

    Returns:
        Path to the written config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[db]",
                f'url = "{dsn}"',
                "",
                "[observability]",
                'log_level = "warning"',
                'log_format = "human"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


@asynccontextmanager
async def _seed_store(dsn: str) -> AsyncIterator[PostgresStore]:
    """Yield a connected store for seeding the already-migrated test database.

    Args:
        dsn: PostgreSQL DSN of a freshly migrated database (``fresh_pg_dsn``).

    Yields:
        Connected PostgresStore.
    """
    store = PostgresStore(dsn)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


async def _execute(dsn: str, sql: str, *args: object) -> None:
    """Run one SQL statement against the test database (fixture backdating).

    Args:
        dsn: PostgreSQL DSN.
        sql: Statement to execute.
        *args: Statement parameters.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


def _stage_a(score: int = _STAGE_A_SCORE) -> StageAResult:
    """Build a minimal completed Stage A result."""
    return StageAResult(
        score=score,
        one_line="fits",
        timing_eligible="yes",
        model="mock/stage-a",
        prompt_hash="prompt-a",
        resume_hash="resume-a",
    )


def _stage_b(verdict: Verdict) -> StageBResult:
    """Build a minimal completed Stage B result with the given verdict."""
    return StageBResult(
        verdict=verdict,
        jd_summary="summary",
        fit_analysis=FitAnalysis(score=_STAGE_B_FIT_SCORE, strengths=[], gaps=[]),
        resume_hooks=[],
        model="mock/stage-b",
        prompt_hash="prompt-b",
        resume_hash="resume-b",
    )


# ---------------------------------------------------------------------------
# Insights overview
# ---------------------------------------------------------------------------


async def _seed_overview_fixture(store: PostgresStore, dsn: str) -> dict[str, date]:
    """Seed the overview matrix across two UTC days plus an out-of-window row.

    Day anchors are derived from one ``now`` capture: ``day_2`` carries a job
    discovered exactly at 00:00:00Z (the UTC day-boundary proof) that is
    Stage A-evaluated the same day; ``day_1`` carries two discovered +
    evaluated jobs and the application of the boundary job. The ``old`` job
    (40 days back) appears in the all-time totals but never in the windowed
    series. The boundary job is gate-passed and the old job gate-failed,
    pinning the gate-survivor semantic: ``totals.ml_gate_passed`` counts
    passes only, and the two never-gated jobs count toward neither.

    Args:
        store: Connected store for the migrated test database.
        dsn: Same database's DSN (for evaluation timestamp backdating).

    Returns:
        The two day anchors (``day_2``, ``day_1``) keyed by name.
    """
    now = datetime.now(UTC)
    day_2 = (now - timedelta(days=2)).date()
    day_1 = (now - timedelta(days=1)).date()
    boundary_at = datetime.combine(day_2, time(0, 0, 0), tzinfo=UTC)

    old = await store.save_job(
        make_job("ins-old", discovered_at=now - timedelta(days=40))
    )
    # Totals-only row; never in the daily series. Gate-failed: must not
    # count toward totals.ml_gate_passed (gate survivors only).
    await store.save_ml_gate_result(
        old.job_id, MLGateResult(score=0.1, result="fail", fail_reason="not_swe")
    )

    boundary = await store.save_job(make_job("ins-boundary", discovered_at=boundary_at))
    await store.save_ml_gate_result(
        boundary.job_id, MLGateResult(score=0.9, result="pass")
    )
    await store.save_stage_a(boundary.job_id, _stage_a())
    await store.save_stage_b(boundary.job_id, _stage_b(Verdict.APPLY))
    await store.record_application(
        ApplicationRecord(
            job_id=boundary.job_id,
            applied_at=datetime.combine(day_1, time(10, 0), tzinfo=UTC),
        )
    )

    job_b = await store.save_job(
        make_job("ins-b", discovered_at=datetime.combine(day_1, time(6, 0), tzinfo=UTC))
    )
    await store.save_stage_a(job_b.job_id, _stage_a())
    await store.save_stage_b(job_b.job_id, _stage_b(Verdict.CONSIDER))
    await store.transition_status(
        TransitionRequest(job_id=job_b.job_id, new_status="shortlisted", force=True)
    )

    below = await store.save_job(
        make_job(
            "ins-below", discovered_at=datetime.combine(day_1, time(8, 0), tzinfo=UTC)
        )
    )
    await store.save_stage_a(below.job_id, _stage_a(score=40))
    await store.mark_stage_b_skipped_batch([below.job_id])

    stage_a_at = {
        boundary.job_id: datetime.combine(day_2, time(9, 0), tzinfo=UTC),
        job_b.job_id: datetime.combine(day_1, time(7, 0), tzinfo=UTC),
        below.job_id: datetime.combine(day_1, time(9, 0), tzinfo=UTC),
    }
    for job_id, at in stage_a_at.items():
        await _execute(
            dsn,
            "UPDATE evaluations SET stage_a_at = $1 WHERE job_id = $2",
            at,
            int(job_id),
        )
    return {"day_2": day_2, "day_1": day_1}


async def test_overview_pins_totals_distributions_and_daily_series(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Overview math: all-time totals + distributions, exact UTC day series.

    The boundary job (discovered 00:00:00Z) must bucket into its own UTC day;
    the 40-day-old job counts in totals but not in the windowed series; the
    series contains only days having data. ``totals.ml_gate_passed`` counts
    only the one gate-passed job: the gate-failed job and the two never-gated
    jobs contribute nothing.
    """
    async with _seed_store(fresh_pg_dsn) as store:
        days = await _seed_overview_fixture(store, fresh_pg_dsn)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get(
            "/api/insights/overview", params={"window": _WINDOW_DAYS}
        )

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    assert payload["window_days"] == _WINDOW_DAYS
    assert payload["totals"] == {
        "jobs": 4,
        "ml_gate_passed": 1,
        "evaluated": 3,
        "applied": 1,
    }
    assert payload["verdict_distribution"] == {
        "apply": 1,
        "consider": 1,
        "below_threshold": 1,
    }
    assert payload["status_distribution"] == {
        "new": 1,
        "scored": 1,
        "applied": 1,
        "shortlisted": 1,
    }
    assert payload["daily"] == [
        {
            "day": days["day_2"].isoformat(),
            "discovered": 1,
            "evaluated": 1,
            "applied": 0,
        },
        {
            "day": days["day_1"].isoformat(),
            "discovered": 2,
            "evaluated": 2,
            "applied": 1,
        },
    ]
    assert payload["applications"] == {
        "applied_count": 1,
        "response_count": 0,
        "interview_count": 0,
        "offer_count": 0,
        "rejection_count": 0,
        "median_days_to_response": None,
        "by_resume": {
            "unknown": {
                "sent": 1,
                "responses": 0,
                "interviews": 0,
                "offers": 0,
                "rejections": 0,
            }
        },
    }


async def test_overview_window_validation_and_empty_db_contract(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """window must be within 1..365; an empty DB yields zeros, never nulls.

    Out-of-range windows return the 422 error shape. A valid request against
    the same fresh (unseeded) schema pins the empty-DB contract: all-zero
    totals (including ``ml_gate_passed``), an empty daily series, empty
    distributions, and an empty by-resume table — empty containers, never
    null.
    """
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        too_low = await client.get("/api/insights/overview", params={"window": 0})
        too_high = await client.get("/api/insights/overview", params={"window": 366})
        empty = await client.get(
            "/api/insights/overview", params={"window": _WINDOW_DAYS}
        )

    assert too_low.status_code == HTTP_VALIDATION_ERROR
    assert too_low.json()["error"]["code"] == "validation_error"
    assert too_high.status_code == HTTP_VALIDATION_ERROR

    assert empty.status_code == HTTP_OK, empty.text
    payload = empty.json()
    assert payload["totals"] == {
        "jobs": 0,
        "ml_gate_passed": 0,
        "evaluated": 0,
        "applied": 0,
    }
    assert payload["daily"] == []
    assert payload["verdict_distribution"] == {}
    assert payload["status_distribution"] == {}
    assert payload["applications"]["by_resume"] == {}


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


async def _seed_attention_fixture(store: PostgresStore, dsn: str) -> dict[str, str]:
    """Seed one job per attention bucket: the three workflow buckets plus
    the three needs_attention pipeline-health categories.

    Args:
        store: Connected store for the migrated test database.
        dsn: Same database's DSN (for ghost-clock backdating).

    Returns:
        Mapping of bucket name -> store job id.
    """
    now = datetime.now(UTC)
    ids: dict[str, str] = {}

    followup = await store.save_job(make_job("att-followup", discovered_at=now))
    await store.transition_status(
        TransitionRequest(job_id=followup.job_id, new_status="applied", force=True)
    )
    await store.set_followup(job_id=followup.job_id, at=now - timedelta(days=1))
    ids["follow_up_today"] = followup.job_id

    interview = await store.save_job(make_job("att-interview", discovered_at=now))
    await store.transition_status(
        TransitionRequest(job_id=interview.job_id, new_status="applied", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=interview.job_id, new_status="interviewing")
    )
    await store.add_interview_round(
        job_id=interview.job_id, label="Onsite", scheduled_at=now + timedelta(days=2)
    )
    ids["interview_prep"] = interview.job_id

    ghost = await store.save_job(make_job("att-ghost", discovered_at=now))
    await store.transition_status(
        TransitionRequest(job_id=ghost.job_id, new_status="applied", force=True)
    )
    # Into the going-ghosted warn window (30 - 5 = 25 days) without crossing
    # the 30-day auto-ghost line; clear the follow-up so buckets stay disjoint.
    await _execute(
        dsn,
        "UPDATE job_status SET last_status_change_at = $1, next_followup_at = NULL"
        " WHERE job_id = $2",
        now - timedelta(days=29),
        int(ghost.job_id),
    )
    ids["going_ghosted"] = ghost.job_id

    enrich = await store.save_job(
        make_job("att-enrich", discovered_at=now, enrich_error="fetch failed")
    )
    ids["enrich_errors"] = enrich.job_id

    low_quality = await store.save_job(
        make_job(
            "att-lowq",
            discovered_at=now,
            jd_text="short jd",
            jd_quality=QualityBand.STUB,
        )
    )
    await store.save_stage_a(low_quality.job_id, _stage_a())
    ids["low_quality_scored"] = low_quality.job_id

    stuck = await store.save_job(make_job("att-stuck", discovered_at=now))
    for _ in range(_STUCK_ERROR_COUNT):
        await store.save_stage_a_error(stuck.job_id, "llm timeout")
    ids["stuck_scoring"] = stuck.job_id
    return ids


async def test_attention_mirrors_digest_footer_buckets(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """GET /api/attention carries the digest footer's six buckets verbatim.

    Every bucket holds exactly its seeded job, with the store's reason or
    detail strings; the payload matches direct ``workflow_attention()`` +
    ``needs_attention()`` calls (default thresholds) on the same fixture.
    """
    async with _seed_store(fresh_pg_dsn) as store:
        ids = await _seed_attention_fixture(store, fresh_pg_dsn)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.get("/api/attention")

    assert response.status_code == HTTP_OK, response.text
    payload = response.json()
    for bucket, job_id in ids.items():
        entries = payload[bucket]
        assert [entry["job_id"] for entry in entries] == [job_id], bucket

    follow_up = payload["follow_up_today"][0]
    assert follow_up["reason"] == "follow-up due"
    assert follow_up["status"] == "applied"
    assert follow_up["days_since"] == 1
    assert payload["interview_prep"][0]["reason"] == "interview prep"
    assert payload["going_ghosted"][0]["reason"] == "going silent"
    assert payload["enrich_errors"][0]["category"] == "enrich_error"
    assert payload["enrich_errors"][0]["detail"] == "fetch failed"
    assert payload["low_quality_scored"][0]["detail"] == "quality=stub"
    assert payload["stuck_scoring"][0]["detail"] == (
        f"stage_a_errors={_STUCK_ERROR_COUNT}, stage_b_errors=0"
    )

    # Parity with the same two store calls the digest footer makes.
    async with _seed_store(fresh_pg_dsn) as store:
        attention = await store.workflow_attention()
        report = await store.needs_attention()
    assert [i.job_id for i in attention.follow_up_today] == [ids["follow_up_today"]]
    assert [i.job_id for i in attention.interview_prep] == [ids["interview_prep"]]
    assert [i.job_id for i in attention.going_ghosted] == [ids["going_ghosted"]]
    assert [i.job_id for i in report.enrich_errors] == [ids["enrich_errors"]]
    assert [i.job_id for i in report.low_quality_scored] == [ids["low_quality_scored"]]
    assert [i.job_id for i in report.stuck_scoring] == [ids["stuck_scoring"]]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _run(run_id: str, started_at: datetime, **overrides: object) -> PipelineRun:
    """Build a PipelineRun fixture with optional counter overrides."""
    kwargs: dict[str, object] = {
        "run_id": run_id,
        "started_at": started_at,
        "source": "ats",
        **overrides,
    }
    return PipelineRun(**kwargs)  # type: ignore[arg-type]


_FULL_COUNTERS: dict[str, object] = {
    "jobs_discovered": 10,
    "jobs_inserted": 4,
    "jobs_updated": 3,
    "jobs_filtered": 2,
    "jobs_ml_gated": 1,
    "stage_a_scored": 5,
    "stage_b_scored": 2,
    "jobs_scored": 5,
    "total_llm_cost_usd": 0.42,
    "errors": 1,
}


_SEEDED_RUN_COUNT = 4


async def _seed_runs(store: PostgresStore) -> datetime:
    """Record four runs: three distinct start times plus one started_at tie.

    Returns:
        The newest run's ``started_at``.
    """
    newest = datetime.now(UTC).replace(microsecond=0)
    await store.record_pipeline_run(_run("run-old", newest - timedelta(hours=2)))
    await store.record_pipeline_run(_run("run-tie-a", newest - timedelta(hours=1)))
    await store.record_pipeline_run(_run("run-tie-b", newest - timedelta(hours=1)))
    await store.record_pipeline_run(
        _run(
            "run-new",
            newest,
            finished_at=newest + timedelta(minutes=5),
            **_FULL_COUNTERS,
        )
    )
    return newest


async def test_runs_list_days_window_filters_and_counts(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """GET /api/runs?days=N lists and counts only runs inside the window."""
    async with _seed_store(fresh_pg_dsn) as store:
        newest = datetime.now(UTC).replace(microsecond=0)
        await store.record_pipeline_run(_run("run-recent", newest - timedelta(hours=2)))
        await store.record_pipeline_run(_run("run-stale", newest - timedelta(days=10)))
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        windowed = await client.get("/api/runs", params={"days": 7})
        alltime = await client.get("/api/runs")

    assert windowed.status_code == HTTP_OK, windowed.text
    payload = windowed.json()
    assert [run["run_id"] for run in payload["runs"]] == ["run-recent"]
    assert payload["total"] == 1
    both = 2
    assert alltime.json()["total"] == both


async def test_runs_list_newest_first_with_pagination(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Runs list newest-first (run_id breaking started_at ties) with totals."""
    async with _seed_store(fresh_pg_dsn) as store:
        newest = await _seed_runs(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        full = await client.get("/api/runs")
        page_one = await client.get("/api/runs", params={"limit": 2, "offset": 0})
        page_two = await client.get("/api/runs", params={"limit": 2, "offset": 2})

    assert full.status_code == HTTP_OK, full.text
    payload = full.json()
    assert [run["run_id"] for run in payload["runs"]] == [
        "run-new",
        "run-tie-b",
        "run-tie-a",
        "run-old",
    ]
    assert payload["total"] == _SEEDED_RUN_COUNT
    newest_run = payload["runs"][0]
    for counter, value in _FULL_COUNTERS.items():
        assert newest_run[counter] == value, counter
    assert newest_run["source"] == "ats"
    assert datetime.fromisoformat(newest_run["started_at"]) == newest
    assert newest_run["finished_at"] is not None
    assert "dry_run_preview" not in newest_run

    assert [r["run_id"] for r in page_one.json()["runs"]] == ["run-new", "run-tie-b"]
    assert page_one.json()["total"] == _SEEDED_RUN_COUNT
    assert [r["run_id"] for r in page_two.json()["runs"]] == ["run-tie-a", "run-old"]
    assert page_two.json()["total"] == _SEEDED_RUN_COUNT


async def test_run_detail_round_trips_and_unknown_is_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """GET /api/runs/{run_id} round-trips counters; unknown id is a 404."""
    async with _seed_store(fresh_pg_dsn) as store:
        await _seed_runs(store)
    app = create_web_app(_write_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        found = await client.get("/api/runs/run-new")
        missing = await client.get("/api/runs/no-such-run")

    assert found.status_code == HTTP_OK, found.text
    run = found.json()
    assert run["run_id"] == "run-new"
    for counter, value in _FULL_COUNTERS.items():
        assert run[counter] == value, counter

    assert missing.status_code == HTTP_NOT_FOUND
    error = missing.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert error["request_id"]
