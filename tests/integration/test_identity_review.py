"""Human duplicate decisions are independent from evaluation eligibility."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from jobfeed.adapters.store._sqlite_real_job_identity import (
    _review,
    resolve_sqlite_real_job,
)
from jobfeed.adapters.store._sqlite_values import _job_from_row
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.models import JobPosting, QualityBand


async def seed(store):
    ids = []
    for number in (1, 2):
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id=f"review-{number}",
                url=f"https://www.linkedin.com/jobs/view/{number}/",
                title="Software Engineer",
                company="Example",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_quality=QualityBand.FULL,
                jd_text=(f"Build production systems version {number}. " * 15),
            )
        )
        ids.append(
            (int(await store.resolve_real_job_id(saved.job_id)), int(saved.job_id))
        )
    async with store._lifecycle.connection() as db:
        await db.execute(
            "UPDATE real_jobs SET identity_review_state='requirements_conflict'"
        )
        await db.execute(
            "INSERT INTO real_job_review_cases(left_real_job_id,right_real_job_id,"
            "left_job_id,right_job_id,reason) VALUES(?,?,?,?,?)",
            (
                ids[0][0],
                ids[1][0],
                ids[0][1],
                ids[1][1],
                "multiple_identity_candidates",
            ),
        )
    return ids


@pytest.mark.asyncio
async def test_pending_identity_can_score_and_keep_separate_is_durable(tmp_path):
    store = SQLiteStore(tmp_path / "review.db")
    await store.connect()
    try:
        ids = await seed(store)
        page = await store.identity_review(status="pending", limit=25, offset=0)
        assert page["total"] == 1
        assert len(await store.claim_real_job_stage_a_by_ids([str(ids[0][0])])) == 1
        await store.identity_decide(str(ids[0][0]), str(ids[1][0]), decision="separate")
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
        assert (await store.identity_review(status="reviewed", limit=25, offset=0))[
            "total"
        ] == 1

        async with store._lifecycle.connection() as db:
            await _review(
                db,
                (ids[0][0], ids[1][0]),
                (ids[0][1], ids[1][1]),
                "multiple_identity_candidates",
            )
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_merge_preserves_sources_and_records_chosen_target(tmp_path):
    store = SQLiteStore(tmp_path / "merge.db")
    await store.connect()
    try:
        ids = await seed(store)
        await store.identity_decide(
            str(ids[0][0]), str(ids[1][0]), decision="merge", target_id=str(ids[1][0])
        )
        assert await store.resolve_real_job_id(str(ids[0][1])) == str(ids[1][0])
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
        history = await store.identity_review(status="reviewed", limit=25, offset=0)
        assert history["items"][0]["target_id"] == str(ids[1][0])
        with pytest.raises(ValueError):
            await store.identity_decide(
                str(ids[0][0]), str(ids[1][0]), decision="merge", target_id="999"
            )
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["applied", "archived"])
async def test_review_never_requeues_application_or_archived_job(tmp_path, status):
    store = SQLiteStore(tmp_path / "workflow.db")
    await store.connect()
    try:
        ids = await seed(store)
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_job_status SET status=? WHERE real_job_id=?",
                (status, ids[0][0]),
            )
        assert await store.claim_real_job_stage_a_by_ids([str(ids[0][0])]) == []
        await store.identity_decide(str(ids[0][0]), str(ids[1][0]), decision="separate")
        assert await store.claim_real_job_stage_a_by_ids([str(ids[0][0])]) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_active_merge_is_rejected_without_losing_pending_pair(tmp_path):
    store = SQLiteStore(tmp_path / "active.db")
    await store.connect()
    try:
        ids = await seed(store)
        await store.claim_real_job_stage_a_by_ids([str(ids[0][0])])
        with pytest.raises(ValueError, match="Scoring is running"):
            await store.identity_decide(
                str(ids[0][0]),
                str(ids[1][0]),
                decision="merge",
                target_id=str(ids[0][0]),
            )
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 1
        assert (await store.identity_review(status="reviewed", limit=25, offset=0))[
            "total"
        ] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_separate_survives_matching_content_refresh(tmp_path):

    store = SQLiteStore(tmp_path / "refresh.db")
    await store.connect()
    try:
        ids = await seed(store)
        await store.identity_decide(str(ids[0][0]), str(ids[1][0]), decision="separate")
        async with store._lifecycle.connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "UPDATE jobs SET jd_text=(SELECT jd_text FROM jobs WHERE id=?) "
                "WHERE id=?",
                (ids[0][1], ids[1][1]),
            )
            row = await (
                await db.execute("SELECT * FROM jobs WHERE id=?", (ids[1][1],))
            ).fetchone()
            assert not await resolve_sqlite_real_job(db, ids[1][1], _job_from_row(row))
        assert await store.resolve_real_job_id(
            str(ids[0][1])
        ) != await store.resolve_real_job_id(str(ids[1][1]))
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pending_excludes_historical_and_inactive_pairs(tmp_path):
    store = SQLiteStore(tmp_path / "scope.db")
    await store.connect()
    try:
        await seed(store)
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_jobs SET identity_review_state='identity_conflict'"
            )
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
        async with store._lifecycle.connection() as db:
            await db.execute("UPDATE real_jobs SET identity_review_state='clear'")
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_jobs SET identity_review_state='requirements_conflict'"
            )
            await db.execute("UPDATE real_job_status SET status='ignored'")
        assert (await store.identity_review(status="pending", limit=25, offset=0))[
            "total"
        ] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_review_uses_current_filters_but_keeps_old_candidate(tmp_path):

    store = SQLiteStore(tmp_path / "filter.db")
    await store.connect()
    try:
        ids = await seed(store)
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_job_status SET status='ignored' WHERE real_job_id=?",
                (ids[1][0],),
            )
        page = await store.identity_review(status="pending", limit=25, offset=0)
        assert page["total"] == 1
        assert page["job_total"] == 1
        assert page["pending_job_ids"] == [str(ids[0][0])]
        assert page["items"][0]["right"]["id"] == str(ids[1][0])
        assert (
            await store.identity_review(
                status="pending",
                limit=25,
                offset=0,
                hard_filters=HardFilters(company_blocklist=["Example"]),
            )
        )["total"] == 0
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_jobs SET first_discovered_at='2020-01-01T00:00:00+00:00',"
                "canonical_posted_at='2020-01-01T00:00:00+00:00'"
            )
        assert (
            await store.identity_review(
                status="pending",
                limit=25,
                offset=0,
                hard_filters=HardFilters(posted_within_days=14),
            )
        )["total"] == 0
    finally:
        await store.close()
