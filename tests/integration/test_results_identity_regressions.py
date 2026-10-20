"""Real multi-city listings share one application and workflow parent."""

from dataclasses import replace

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from tests.support.sqlite_jobs_evaluations import make_job

EPIC = "https://epic.avature.net/Careers/RegisterMethod?folderId=30063&source=LinkedIn"
SOURCE_COUNT = 2
BODY = "Build and maintain distributed production services with Python and SQL. " * 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "apply_url",
    [
        EPIC,
        "https://ad.doubleclick.net/ddm/clk/123;m?https://jobs-us.pwc.com/us/en/job/"
        "PUVPUIUS750493WDEXTERNALENUS/Engineer?utm_source=linkedin",
        "https://www.synchronycareers.com/job/-/-/49398/101972674880?source=LinkedIn",
        "https://jobs.smartrecruiters.com/Wise/744000151030889-graduate-software-engineer",
    ],
)
async def test_multicity_reingest_preserves_applied_parent(tmp_path, apply_url):
    store = SQLiteStore(tmp_path / "identity.db")
    await store.connect()
    try:
        first = await store.save_job(
            replace(
                make_job("first", jd_text=BODY),
                platform="linkedin",
                canonical_id="123",
                url="https://www.linkedin.com/jobs/view/123/",
                apply_url=apply_url,
                location="Manhattan, NY",
            )
        )
        parent = await store.resolve_real_job_id(first.job_id)
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_job_status SET status='applied' WHERE real_job_id=?",
                (parent,),
            )
            await db.execute(
                "INSERT INTO real_job_applications"
                "(real_job_id,source_job_id,applied_at) VALUES(?,?,?)",
                (parent, first.job_id, "2026-10-19T00:00:00Z"),
            )
        incoming = replace(
            make_job("second", jd_text=BODY),
            platform="linkedin",
            canonical_id="456",
            url="https://www.linkedin.com/jobs/view/456/",
            apply_url=apply_url,
            location="New York, NY",
        )
        second = await store.save_job(incoming)
        await store.save_job(incoming)
        assert await store.resolve_real_job_id(second.job_id) == parent
        async with store._lifecycle.connection() as db:
            assert (
                await (
                    await db.execute(
                        "SELECT status FROM real_job_status WHERE real_job_id=?",
                        (parent,),
                    )
                ).fetchone()
            )[0] == "applied"
            assert (
                await (
                    await db.execute(
                        "SELECT count(*) FROM real_job_applications "
                        "WHERE real_job_id=?",
                        (parent,),
                    )
                ).fetchone()
            )[0] == 1
            assert (
                await (
                    await db.execute(
                        "SELECT count(*) FROM jobs WHERE real_job_id=?", (parent,)
                    )
                ).fetchone()
            )[0] == SOURCE_COUNT
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_content_match_after_fifty_unrelated_candidates(tmp_path):
    store = SQLiteStore(tmp_path / "crowded.db")
    await store.connect()
    try:
        for n in range(55):
            await store.save_job(
                make_job(f"unrelated-{n}", jd_text=f"Different role {n}")
            )
        first = await store.save_job(make_job("matching-one", jd_text=BODY))
        second = await store.save_job(make_job("matching-two", jd_text=BODY))
        assert await store.resolve_real_job_id(
            first.job_id
        ) == await store.resolve_real_job_id(second.job_id)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard", ["separate", "status_conflict", "different_requisition", "none"]
)
async def test_historical_backfill_respects_guards(tmp_path, guard):
    store = SQLiteStore(tmp_path / "backfill.db")
    await store.connect()
    try:
        sources = []
        parents = []
        for n in range(2):
            saved = await store.save_job(
                replace(
                    make_job(str(n), jd_text=BODY),
                    platform="linkedin",
                    canonical_id=str(100 + n),
                    url=f"https://www.linkedin.com/jobs/view/{100 + n}/",
                    location=f"City {n}",
                )
            )
            sources.append(saved.job_id)
            parents.append(await store.resolve_real_job_id(saved.job_id))
        if guard == "separate":
            async with store._lifecycle.connection() as db:
                await db.execute(
                    "INSERT INTO real_job_review_cases"
                    "(left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason)"
                    " VALUES(?,?,?,?,?)",
                    (*parents, *sources, "multiple_identity_candidates"),
                )
            await store.identity_decide(*parents, decision="separate")
        async with store._lifecycle.connection() as db:
            for n, source in enumerate(sources):
                url = (
                    EPIC
                    if guard != "different_requisition" or n == 0
                    else EPIC.replace("30063", "30064")
                )
                await db.execute(
                    "UPDATE jobs SET apply_url=? WHERE id=?", (url, source)
                )
            await db.execute(
                "UPDATE real_job_status SET status='applied' WHERE real_job_id=?",
                (parents[0],),
            )
            if guard == "status_conflict":
                await db.execute(
                    "UPDATE real_job_status SET status='ignored' WHERE real_job_id=?",
                    (parents[1],),
                )
        await store.backfill_real_job_identifiers(limit=100)
        resolved = [await store.resolve_real_job_id(source) for source in sources]
        assert (resolved[0] == resolved[1]) == (guard == "none")
        async with store._lifecycle.connection() as db:
            assert (
                await (
                    await db.execute(
                        "SELECT status FROM real_job_status WHERE real_job_id=?",
                        (resolved[0],),
                    )
                ).fetchone()
            )[0] == "applied"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_large_historical_destination_group_backfills_idempotently(tmp_path):
    store = SQLiteStore(tmp_path / "large-backfill.db")
    await store.connect()
    source_count = 60
    try:
        for n in range(source_count):
            await store.save_job(
                replace(
                    make_job(str(n), jd_text=BODY),
                    platform="linkedin",
                    canonical_id=str(1000 + n),
                    url=f"https://www.linkedin.com/jobs/view/{1000 + n}/",
                    location=f"City {n}",
                )
            )
        async with store._lifecycle.connection() as db:
            await db.execute("UPDATE jobs SET apply_url=?", (EPIC,))
            await db.execute(
                "UPDATE real_job_status SET status='applied' "
                "WHERE real_job_id=(SELECT max(id) FROM real_jobs)"
            )
        for _ in range(2):
            last = 0
            while True:
                last, count = await store.backfill_real_job_identifiers(
                    after_id=last, limit=20
                )
                if count == 0:
                    break
            async with store._lifecycle.connection() as db:
                assert (
                    await (
                        await db.execute("SELECT count(*) FROM real_jobs")
                    ).fetchone()
                )[0] == 1
                assert (
                    await (await db.execute("SELECT count(*) FROM jobs")).fetchone()
                )[0] == source_count
                assert (
                    await (
                        await db.execute("SELECT status FROM real_job_status")
                    ).fetchone()
                )[0] == "applied"
                assert (await (await db.execute("PRAGMA quick_check")).fetchone())[
                    0
                ] == "ok"
    finally:
        await store.close()
