"""Approved legacy reuse is explicit, preserves source answers and is repeatable."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.store._sqlite_real_job_evaluation import (
    reconcile_legacy_review_page,
)
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand

HIGHEST_SCORE = 90


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_score", "second_time", "winner_index"),
    [
        (80, "2026-05-11T00:00:00Z", 0),
        (90, "2026-05-12T00:00:00Z", 1),
        (90, "2026-05-11T00:00:00Z", 1),
    ],
)
async def test_reconcile_keeps_highest_whole_answer_and_is_idempotent(
    tmp_path: Path,
    second_score: int,
    second_time: str,
    winner_index: int,
) -> None:
    store = SQLiteStore(tmp_path / "legacy.db")
    await store.connect()
    try:
        ids = []
        for key in ("one", "two"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://www.linkedin.com/jobs/view/{key}/",
                    title="Software Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_quality=QualityBand.FULL,
                    jd_text="Build production software services and APIs. " * 12,
                )
            )
            ids.append(int(saved.job_id))
        rid = int(await store.resolve_real_job_id(str(ids[0])))
        async with store._lifecycle.connection() as db:
            await db.execute("UPDATE jobs SET real_job_id=? WHERE id=?", (rid, ids[1]))
            for sid, score, verdict in (
                (ids[0], 90, "skip"),
                (ids[1], second_score, "apply"),
            ):
                await db.execute(
                    "INSERT INTO "
                    "evaluations(job_id,stage_a_status,stage_a_score,stage_a_one_line,"
                    "stage_a_model,stage_a_at,stage_b_status,stage_b_verdict,"
                    "stage_b_jd_summary) "
                    "VALUES(?,'completed',?,?,'legacy',?,'completed',?,?)",
                    (
                        sid,
                        score,
                        f"explanation-{sid}",
                        "2026-05-11T00:00:00Z" if sid == ids[0] else second_time,
                        verdict,
                        f"summary-{sid}",
                    ),
                )
            await db.execute(
                "UPDATE real_jobs SET "
                "identity_review_state='evaluation_conflict' WHERE id=?",
                (rid,),
            )
            before = [
                tuple(row)
                for row in await (
                    await db.execute("SELECT * FROM evaluations ORDER BY job_id")
                ).fetchall()
            ]
            dry = await reconcile_legacy_review_page(db)
            assert dry[0]["action"] == "adopt_legacy"
            assert dry[0]["score_source_job_id"] == ids[winner_index]
            assert (
                await (
                    await db.execute("SELECT count(*) FROM real_job_evaluations")
                ).fetchone()
            )[0] == 0
            applied = await reconcile_legacy_review_page(db, apply=True)
            assert applied == dry
            assert await reconcile_legacy_review_page(db, apply=True) == []
            row = await (
                await db.execute(
                    "SELECT * FROM real_job_evaluations WHERE real_job_id=?", (rid,)
                )
            ).fetchone()
            assert row["stage_a_score"] == HIGHEST_SCORE
            assert row["stage_a_one_line"] == f"explanation-{ids[winner_index]}"
            assert row["stage_b_verdict"] == ("skip" if winner_index == 0 else "apply")
            assert (
                json.loads(row["stage_b_json"])["jd_summary"]
                == f"summary-{ids[winner_index]}"
            )
            assert [
                tuple(row)
                for row in await (
                    await db.execute("SELECT * FROM evaluations ORDER BY job_id")
                ).fetchall()
            ] == before
            adoption = json.loads(
                (
                    await (
                        await db.execute(
                            "SELECT value FROM state WHERE key=?",
                            (f"real-job-legacy-adoption:{rid}:1",),
                        )
                    ).fetchone()
                )[0]
            )
            assert adoption["score_source_job_id"] == ids[winner_index]
        policies = {
            "stage_a_policy": {"model": "current-a"},
            "stage_b_policy": {"model": "current-b"},
        }
        detail = await store.get_real_job_view(str(rid), **policies)
        assert detail["row"]["stage_a_score"] == HIGHEST_SCORE
        assert str(rid) not in await store.list_real_job_ids_for_evaluation(
            limit=100, **policies
        )
        assert await store.claim_real_job_stage_a_by_ids([str(rid)], **policies) == []
        assert (
            await store.claim_real_job_stage_b_by_ids(
                [str(rid)], stage_a_threshold=0, **policies
            )
            == []
        )
        await store.set_state("real_job_evaluation_activation_v1", "enabled")
        for _ in range(2):
            await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id="one",
                    url="https://www.linkedin.com/jobs/view/one/",
                    title="Software Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_quality=QualityBand.FULL,
                    jd_text="Build production software services and APIs. " * 12,
                )
            )
        async with store._lifecycle.connection() as db:
            assert (
                await (
                    await db.execute(
                        "SELECT identity_review_state FROM real_jobs WHERE id=?", (rid,)
                    )
                ).fetchone()
            )[0] == "clear"
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing", "official_conflict", "manual_conflict", "canonical_changed"]
)
async def test_reconcile_preserves_unresolved_holds_and_actual_input_invalidation(
    tmp_path: Path, case: str
) -> None:
    store = SQLiteStore(tmp_path / "preserve.db")
    await store.connect()
    try:
        source = JobPosting(
            platform="linkedin",
            canonical_id="one",
            url="https://boards.greenhouse.io/acme/jobs/1234567",
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=datetime.now(UTC),
            jd_quality=QualityBand.FULL,
            jd_text="Build production software services and APIs. " * 12,
        )
        first = await store.save_job(source)
        rid = int(await store.resolve_real_job_id(first.job_id))
        if case == "canonical_changed":
            await store.claim_real_job_stage_a_by_ids([str(rid)])
        async with store._lifecycle.connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "UPDATE real_jobs SET identity_review_state=? WHERE id=?",
                (
                    "evaluation_input_missing"
                    if case == "missing"
                    else "requirements_conflict",
                    rid,
                ),
            )
            if case == "missing":
                await db.execute(
                    "UPDATE jobs SET jd_text='' WHERE id=?", (int(first.job_id),)
                )
            elif case == "official_conflict":
                # Both sources are trusted, so the preference rule cannot resolve it.
                await db.execute(
                    "INSERT INTO jobs(platform,canonical_id,url,title,company,loc"
                    "ation,discovered_at,jd_quality,jd_text,real_job_id) SELECT '"
                    "jobright','two','https://jobs.lever.co/acme/12345678-1234-12"
                    "34-1234-123456789012',title,company,location,discovered_at,'"
                    "full',?,real_job_id FROM jobs WHERE id=?",
                    ("Design FPGA hardware and verify RTL. " * 15, int(first.job_id)),
                )
            elif case == "manual_conflict":
                await db.execute(
                    "INSERT INTO real_job_review_cases(left_real_job_id,right_rea"
                    "l_job_id,left_job_id,right_job_id,reason) VALUES(?,?,?,?, 'e"
                    "xplicit_status_conflict')",
                    (rid, rid, int(first.job_id), int(first.job_id)),
                )
            else:
                await db.execute(
                    "UPDATE real_job_evaluations SET stage_a_status='completed',s"
                    "tage_a_score=90 WHERE real_job_id=?",
                    (rid,),
                )
                await db.execute(
                    "UPDATE jobs SET jd_text=? WHERE id=?",
                    (
                        "Build changed production software and APIs. " * 12,
                        int(first.job_id),
                    ),
                )
            preview = await reconcile_legacy_review_page(db)
            applied = await reconcile_legacy_review_page(db, apply=True)
            assert applied == preview
            if case == "canonical_changed":
                assert applied[0]["action"] == "clear_hold_invalidate_changed_input"
                current = await (
                    await db.execute(
                        "SELECT stage_a_status,stage_a_score FROM real_job_evaluation"
                        "s WHERE real_job_id=?",
                        (rid,),
                    )
                ).fetchone()
                assert tuple(current) == (None, None)
                assert (
                    await (
                        await db.execute(
                            "SELECT reason FROM real_job_evaluation_history "
                            "WHERE real_job_id=?",
                            (rid,),
                        )
                    ).fetchone()
                )[0] == "input_changed"
            else:
                assert applied[0]["action"] == "keep_hold"
                assert (
                    await (
                        await db.execute(
                            "SELECT identity_review_state FROM real_jobs WHERE id=?",
                            (rid,),
                        )
                    ).fetchone()
                )[0] != "clear"
    finally:
        await store.close()
