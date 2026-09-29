"""Backlog SQL removes impossible candidates without rebuilding source inputs."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.scoring import MAX_STAGE_RETRIES


@pytest.mark.parametrize("stage", ["a", "b"])
async def test_sql_backlog_excludes_unclaimable_without_policy_requeue(
    tmp_path: Path,
    stage: str,
) -> None:
    now = datetime.now(UTC)
    store = SQLiteStore(tmp_path / "selection.db")
    await store.connect()
    expected = []
    try:
        for kind in (
            "pending",
            "old",
            "partial",
            "closed",
            "repost",
            "active",
            "exhausted",
            "stale",
            "retry",
            "changed",
        ):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=kind,
                    url=f"https://example.test/{kind}",
                    title="Engineer",
                    company=kind,
                    location="Remote",
                    discovered_at=now - timedelta(days=60) if kind == "old" else now,
                    jd_text="Build production services. " * 20
                    if kind != "partial"
                    else "",
                    jd_quality=QualityBand.FULL
                    if kind != "partial"
                    else QualityBand.PARTIAL,
                    is_repost=kind == "repost",
                )
            )
            real_id = await store.resolve_real_job_id(saved.job_id)
            status = (
                "in_progress"
                if kind in {"active", "stale"}
                else (
                    "error" if kind in {"exhausted", "retry", "changed"} else "pending"
                )
            )
            async with store._lifecycle.connection() as db:
                if kind == "closed":
                    await db.execute(
                        "UPDATE real_jobs SET official_closed_at=? WHERE id=?",
                        (now.isoformat(), int(real_id)),
                    )
                await db.execute(
                    "INSERT INTO real_job_evaluations(real_job_id,source_job_id,"
                    "input_jd_text,input_facts_json,stage_a_status,stage_a_score,"
                    "stage_b_status,stage_a_error_count,stage_b_error_count,"
                    "updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        int(real_id),
                        int(saved.job_id),
                        "Build production services. " * 20,
                        json.dumps(
                            {
                                f"stage_{stage}_policy": {
                                    "model": "old" if kind == "changed" else "current"
                                }
                            }
                        ),
                        status if stage == "a" else "completed",
                        90,
                        status if stage == "b" else None,
                        MAX_STAGE_RETRIES if kind in {"exhausted", "changed"} else 1,
                        MAX_STAGE_RETRIES if kind in {"exhausted", "changed"} else 1,
                        (
                            now - timedelta(hours=2) if kind == "stale" else now
                        ).isoformat(),
                    ),
                )
                await db.commit()
            if kind in {"pending", "stale", "retry"}:
                expected.append(real_id)
        actual = await store.list_real_job_ids_for_evaluation(
            limit=100,
            stage=stage,
            max_days=30,
            stage_a_policy={"model": "current"},
            stage_b_policy={"model": "current"},
        )
        assert actual == list(reversed(expected))
    finally:
        await store.close()


async def test_date_prefilter_does_not_use_clamped_display_date(tmp_path: Path) -> None:
    """The SQL necessary condition must not reject an exact-claim eligible input."""
    now = datetime.now(UTC)
    store = SQLiteStore(tmp_path / "dates.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="date-boundary",
                url="https://example.test/date-boundary",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=now - timedelta(days=60),
                posted_at=now - timedelta(days=1),
                jd_text="Build production services. " * 20,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert await store.list_real_job_ids_for_evaluation(
            limit=100, stage="a", max_days=30
        ) == [real_id]
        claims = await store.claim_real_job_stage_a_by_ids([real_id], max_days=30)
        assert [item.real_job_id for item in claims] == [real_id]
    finally:
        await store.close()
