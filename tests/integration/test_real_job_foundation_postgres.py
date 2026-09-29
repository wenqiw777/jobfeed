"""PostgreSQL source writer assigns exactly one canonical parent per source."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store import postgres as postgres_module
from jobfeed.adapters.store._postgres_real_job_identity import (
    IdentityParentChanged,
    _merge,
    _status_conflict,
)
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import JobPosting, QualityBand, TransitionRequest

pytestmark = pytest.mark.postgres
EXPECTED_SEPARATE_REAL_JOBS = 2


async def test_rescan_keeps_one_parent_and_identifier(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        posting = JobPosting(
            platform="linkedin",
            canonical_id="123456789",
            url="https://linkedin.com/jobs/view/123456789",
            title="Engineer",
            company="Example",
            location="Detroit",
            discovered_at=datetime.now(UTC),
        )
        first = await store.save_job(posting)
        again = await store.save_job(posting)
        assert first.job_id == again.job_id
        async with store._get_pool().acquire() as conn:
            parent_id = await conn.fetchval(
                "SELECT real_job_id FROM jobs WHERE id=$1", int(first.job_id)
            )
            assert parent_id is not None
            assert await conn.fetchval("SELECT COUNT(*) FROM real_jobs") == 1
            assert await conn.fetchval("SELECT COUNT(*) FROM real_job_identifiers") == 1
    finally:
        await store.close()


async def test_strict_content_aliases(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        first_job = JobPosting(
            platform="linkedin",
            canonical_id="li-1",
            url="https://example.test/li",
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=datetime.now(UTC),
            jd_text=body,
        )
        first = await store.save_job(first_job)
        second = await store.save_job(
            replace(
                first_job,
                platform="jobright",
                canonical_id="jr-1",
                url="https://example.test/jr",
            )
        )
        async with store._get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT real_job_id FROM jobs WHERE id=ANY($1::int[]) ORDER BY id",
                [int(first.job_id), int(second.job_id)],
            )
            assert rows[0][0] == rows[1][0]
    finally:
        await store.close()


async def test_scoped_ats_alias_and_explicit_conflict(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ats = "https://qualcomm.eightfold.ai/careers?pid=446721162271"
        base = JobPosting(
            platform="linkedin",
            canonical_id="li-1",
            url="https://linkedin.com/jobs/view/4469009469/",
            apply_url=ats,
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=datetime.now(UTC),
            jd_text="LinkedIn extract",
        )
        first = await store.save_job(base)
        second = await store.save_job(
            replace(
                base,
                platform="jobright",
                canonical_id="jr-1",
                url=ats,
                apply_url=None,
                jd_text="Jobright extract",
            )
        )
        async with store._get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT real_job_id FROM jobs WHERE id=ANY($1::int[]) ORDER BY id",
                [int(first.job_id), int(second.job_id)],
            )
            assert rows[0][0] == rows[1][0]
    finally:
        await store.close()


async def test_shared_ats_overrides_conflicting_aggregator_without_score(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        ats = "https://qualcomm.eightfold.ai/careers?pid=446721162271"
        first = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="li-requirements",
                url="https://www.linkedin.com/jobs/view/4469009469/",
                apply_url=ats,
                title="Backend SWE",
                company="Qualcomm",
                location="San Diego, CA",
                discovered_at=datetime.now(UTC),
                jd_text=body,
                jd_quality=QualityBand.FULL,
            )
        )
        second = await store.save_job(
            JobPosting(
                platform="jobright",
                canonical_id="jr-requirements",
                url=ats,
                title="Backend SWE",
                company="Qualcomm",
                location="San Diego, CA",
                discovered_at=datetime.now(UTC),
                jd_text=body.replace("distributed systems", "mobile apps"),
                jd_quality=QualityBand.FULL,
            )
        )
        async with store._get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT id,real_job_id FROM jobs WHERE id=ANY($1::int[]) ORDER BY id",
                [int(first.job_id), int(second.job_id)],
            )
            assert rows[0]["real_job_id"] == rows[1]["real_job_id"]
            parent = int(rows[0]["real_job_id"])
            assert (
                await conn.fetchval(
                    "SELECT identity_review_state FROM real_jobs WHERE id=$1", parent
                )
                == "clear"
            )
            cases = await conn.fetch(
                "SELECT left_job_id,right_job_id,reason FROM real_job_review_cases "
                "WHERE left_real_job_id=$1 AND right_real_job_id=$1",
                parent,
            )
            assert cases == []
            assert (
                await conn.fetchval(
                    "SELECT 1 FROM real_job_evaluations WHERE real_job_id=$1", parent
                )
                is None
            )
    finally:
        await store.close()


async def test_concurrent_transitive_merge_rejects_stale_parent(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ids = []
        for native in ("race-a", "race-b", "race-c"):
            result = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=native,
                    url=f"https://example.test/{native}",
                    title="Engineer",
                    company="Example",
                    location="Detroit",
                    discovered_at=datetime.now(UTC),
                )
            )
            ids.append(int(result.job_id))
        async with store._get_pool().acquire() as conn:
            parents = [
                int(row["real_job_id"])
                for row in await conn.fetch(
                    "SELECT real_job_id FROM jobs WHERE id=ANY($1::int[]) ORDER BY id",
                    ids,
                )
            ]
        first_done = asyncio.Event()
        release_first = asyncio.Event()

        async def merge_first() -> None:
            async with store._get_pool().acquire() as conn, conn.transaction():
                await _merge(
                    conn,
                    parents[0],
                    parents[1],
                    source_id=ids[0],
                    other_source_id=ids[1],
                )
                first_done.set()
                await release_first.wait()

        async def merge_second() -> str:
            await first_done.wait()
            async with store._get_pool().acquire() as conn, conn.transaction():
                try:
                    await _merge(
                        conn,
                        parents[1],
                        parents[2],
                        source_id=ids[1],
                        other_source_id=ids[2],
                    )
                except RuntimeError as exc:
                    return str(exc)
            return "merged-stale-parent"

        first_task = asyncio.create_task(merge_first())
        second_task = asyncio.create_task(merge_second())
        await asyncio.sleep(0.05)
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)
        assert second_task.result() == "real-job parent changed during merge"
        async with store._get_pool().acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM jobs WHERE real_job_id IS NULL"
                )
                == 0
            )
            assert (
                await conn.fetchval("SELECT COUNT(*) FROM real_jobs")
                == EXPECTED_SEPARATE_REAL_JOBS
            )
    finally:
        await store.close()


async def test_merge_rechecks_explicit_status_after_waiting_for_writer(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ids = []
        for native in ("status-race-left", "status-race-right"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=native,
                    url=f"https://example.test/{native}",
                    title="Engineer",
                    company="Example",
                    location="Detroit",
                    discovered_at=datetime.now(UTC),
                )
            )
            ids.append(int(saved.job_id))
        parents = [int(await store.resolve_real_job_id(str(source))) for source in ids]
        await store.transition_real_job_status(
            TransitionRequest(job_id=str(parents[0]), new_status="ignored")
        )
        writer_ready = asyncio.Event()
        release_writer = asyncio.Event()

        async def write_applied() -> None:
            async with store._get_pool().acquire() as conn, conn.transaction():
                await store._transition_real_in_tx(
                    conn,
                    TransitionRequest(job_id=str(parents[1]), new_status="applied"),
                )
                writer_ready.set()
                await release_writer.wait()

        async def merge_after_stale_precheck() -> None:
            async with store._get_pool().acquire() as conn, conn.transaction():
                assert not await _status_conflict(conn, parents[0], parents[1])
                await writer_ready.wait()
                await _merge(
                    conn,
                    parents[0],
                    parents[1],
                    source_id=ids[0],
                    other_source_id=ids[1],
                )

        writer = asyncio.create_task(write_applied())
        await writer_ready.wait()
        merger = asyncio.create_task(merge_after_stale_precheck())
        await asyncio.sleep(0.05)
        release_writer.set()
        await asyncio.wait_for(asyncio.gather(writer, merger), timeout=10)
        async with store._get_pool().acquire() as conn:
            assert await conn.fetchval("SELECT COUNT(*) FROM real_jobs") == len(parents)
            assert {
                row["status"]
                for row in await conn.fetch(
                    "SELECT status FROM real_job_status "
                    "WHERE real_job_id=ANY($1::bigint[])",
                    parents,
                )
            } == {"applied", "ignored"}
            assert (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM real_job_review_cases WHERE "
                    "reason='explicit_status_conflict'"
                )
                == 1
            )
            assert {
                row["to_status"]
                for row in await conn.fetch(
                    "SELECT to_status FROM real_job_status_history WHERE "
                    "real_job_id=ANY($1::bigint[])",
                    parents,
                )
            } >= {"applied", "ignored"}
    finally:
        await store.close()


async def test_source_upsert_retries_parent_move_once(
    fresh_pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        original = postgres_module.resolve_postgres_real_job
        attempts = 0

        async def moved_once(conn, source_id, posting):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise IdentityParentChanged("real-job parent changed during merge")
            await original(conn, source_id, posting)

        monkeypatch.setattr(postgres_module, "resolve_postgres_real_job", moved_once)
        result = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="retry-source",
                url="https://example.test/retry-source",
                title="Engineer",
                company="Example",
                location="Detroit",
                discovered_at=datetime.now(UTC),
            )
        )
        assert attempts == EXPECTED_SEPARATE_REAL_JOBS
        async with store._get_pool().acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM jobs WHERE platform='linkedin' "
                    "AND canonical_id='retry-source'"
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT real_job_id FROM jobs WHERE id=$1", int(result.job_id)
                )
                is not None
            )
    finally:
        await store.close()


async def test_exact_id_backfill_holds_divergent_full_descriptions(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        ats = "https://qualcomm.eightfold.ai/careers?pid=446721162271"
        async with store._get_pool().acquire() as conn:
            await conn.executemany(
                "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
                "jd_text,jd_quality,discovered_at) "
                "VALUES($1,$2,$3,'Backend SWE','Qualcomm','San Diego, CA',"
                "$4,'full',now())",
                [
                    ("linkedin", "li-old", ats, body),
                    (
                        "jobright",
                        "jr-old",
                        ats,
                        body.replace("distributed systems", "mobile apps"),
                    ),
                ],
            )
        assert await store.reconcile_real_jobs() == 0
        assert (await store.backfill_real_job_identifiers(limit=10))[
            1
        ] == EXPECTED_SEPARATE_REAL_JOBS
        async with store._get_pool().acquire() as conn:
            parents = await conn.fetch("SELECT DISTINCT real_job_id FROM jobs")
            assert len(parents) == 1
            parent = parents[0][0]
            assert (
                await conn.fetchval(
                    "SELECT identity_review_state FROM real_jobs WHERE id=$1", parent
                )
                == "requirements_conflict"
            )
            assert [
                row[0]
                for row in await conn.fetch("SELECT reason FROM real_job_review_cases")
            ] == ["requirements_conflict"]
    finally:
        await store.close()


async def test_observed_alias_triples_merge_without_freezing_parent(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        base = JobPosting(
            platform="jobright",
            canonical_id="sw-alias",
            url="https://careers.southwestair.com/us/en/job/"
            "SOUTUSR202672886ENUSEXTERNAL/Software-Engineer",
            title="Associate Software Engineer - Direct College Hire",
            company="Southwest Airline Career Page",
            location="United States",
            discovered_at=datetime.now(UTC),
            jd_text=body,
        )
        southwest = [await store.save_job(base)]
        sw_official_job = replace(
            base,
            canonical_id="sw-official",
            company="Southwest Airlines",
            url=base.url + "?utm_source=feed",
            jd_text="Incomplete early extract",
        )
        southwest.append(await store.save_job(sw_official_job))
        southwest.append(
            await store.save_job(
                replace(
                    base,
                    platform="linkedin",
                    canonical_id="sw-linkedin",
                    company="Southwest Airlines",
                    location="Dallas, TX",
                    url="https://www.linkedin.com/jobs/view/4469044914/",
                )
            )
        )
        await store.save_job(replace(sw_official_job, jd_text=body))
        quick = replace(
            base,
            platform="speedyapply",
            canonical_id="sa-qualcomm",
            url="https://jobright.ai/jobs/info/6ab1a0eb191d8c340dbdbf57",
            company="Qualcomm",
            title="#Backend Software Engineer",
            location="San Diego, CA, United States",
            jd_text="Short extract",
        )
        qualcomm = [await store.save_job(quick)]
        qualcomm.append(
            await store.save_job(
                replace(
                    quick,
                    platform="jobright",
                    canonical_id="6ab1a0eb191d8c340dbdbf57",
                    url="https://qualcomm.eightfold.ai/careers?pid=446721162271",
                    location="San Diego, CA",
                    jd_text=body,
                )
            )
        )
        qualcomm.append(
            await store.save_job(
                replace(
                    quick,
                    platform="linkedin",
                    canonical_id="li-qualcomm",
                    url="https://www.linkedin.com/jobs/view/4469009469/",
                    location="San Diego, CA",
                    jd_text=body,
                )
            )
        )
        async with store._get_pool().acquire() as conn:
            for group in (southwest, qualcomm):
                parents = await conn.fetch(
                    "SELECT DISTINCT real_job_id FROM jobs WHERE id=ANY($1::int[])",
                    [int(saved.job_id) for saved in group],
                )
                assert len(parents) == 1
    finally:
        await store.close()


async def test_review_for_third_parent_survives_other_merge(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        base = JobPosting(
            platform="linkedin",
            canonical_id="first",
            url="https://example.test/first",
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=datetime.now(UTC),
            jd_text="Initially different",
        )
        first = await store.save_job(base)
        second = await store.save_job(
            replace(
                base,
                platform="jobright",
                canonical_id="second",
                url="https://example.test/second",
                jd_text=body,
            )
        )
        disputed = await store.save_job(
            replace(
                base,
                platform="speedyapply",
                canonical_id="disputed",
                url="https://example.test/disputed",
                title="Frontend SWE",
            )
        )
        async with store._get_pool().acquire() as conn:
            parents = [
                await conn.fetchval(
                    "SELECT real_job_id FROM jobs WHERE id=$1", int(saved.job_id)
                )
                for saved in (first, second, disputed)
            ]
            await conn.execute(
                "INSERT INTO real_job_review_cases("
                "left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason) "
                "VALUES($1,$2,$3,$4,'conflicting_role_facts')",
                parents[1],
                parents[2],
                int(second.job_id),
                int(disputed.job_id),
            )
        await store.save_job(replace(base, jd_text=body))
        async with store._get_pool().acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT real_job_id FROM jobs WHERE id=$1", int(second.job_id)
                )
                == parents[0]
            )
            row = await conn.fetchrow(
                "SELECT left_real_job_id,right_real_job_id FROM real_job_review_cases"
            )
            assert (row[0], row[1]) == (parents[0], parents[2])
    finally:
        await store.close()


async def test_distinct_ats_requisitions_stay_separate_through_bridge(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        base = JobPosting(
            platform="jobright",
            canonical_id="ats-one",
            url="https://qualcomm.eightfold.ai/careers?pid=446721162271",
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=datetime.now(UTC),
            jd_text=body,
        )
        first = await store.save_job(base)
        bridge = await store.save_job(
            replace(
                base,
                platform="linkedin",
                canonical_id="li-bridge",
                url="https://www.linkedin.com/jobs/view/4469009999/",
            )
        )
        second = await store.save_job(
            replace(
                base,
                platform="speedyapply",
                canonical_id="ats-two",
                url="https://qualcomm.eightfold.ai/careers?pid=446721162272",
            )
        )
        async with store._get_pool().acquire() as conn:
            parents = [
                await conn.fetchval(
                    "SELECT real_job_id FROM jobs WHERE id=$1", int(saved.job_id)
                )
                for saved in (first, bridge, second)
            ]
        assert parents[0] == parents[1]
        assert parents[0] != parents[2]
    finally:
        await store.close()


async def test_explicit_exact_identity_backfill(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ats = "https://qualcomm.eightfold.ai/careers?pid=446721162271"
        async with store._get_pool().acquire() as conn:
            for platform, native in (("linkedin", "li-old"), ("jobright", "jr-old")):
                await conn.execute(
                    "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
                    "discovered_at) VALUES($1,$2,$3,'Backend SWE','Qualcomm',"
                    "'San Diego, CA',now())",
                    platform,
                    native,
                    ats,
                )
        assert await store.reconcile_real_jobs() == 0
        last, processed = await store.backfill_real_job_identifiers(limit=10)
        assert last > 0 and processed == EXPECTED_SEPARATE_REAL_JOBS
        async with store._get_pool().acquire() as conn:
            assert (
                await conn.fetchval("SELECT COUNT(DISTINCT real_job_id) FROM jobs") == 1
            )
    finally:
        await store.close()


async def test_conflicting_explicit_statuses_hold_merge_for_review(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        body = "Requirements: Python, distributed systems, and API ownership. " * 12
        base = JobPosting(
            platform="linkedin",
            canonical_id="li-1",
            url="https://example.test/li",
            title="Backend SWE",
            company="Qualcomm",
            location="San Diego, CA",
            discovered_at=datetime.now(UTC),
            jd_text=body,
        )
        first = await store.save_job(base)
        other = replace(
            base,
            platform="jobright",
            canonical_id="jr-1",
            url="https://example.test/jr",
            jd_text="Different text",
        )
        second = await store.save_job(other)
        async with store._get_pool().acquire() as conn:
            await conn.execute(
                "UPDATE job_status SET status='applied' WHERE job_id=$1",
                int(first.job_id),
            )
            await conn.execute(
                "UPDATE job_status SET status='ignored' WHERE job_id=$1",
                int(second.job_id),
            )
        await store.save_job(replace(other, jd_text=body))
        async with store._get_pool().acquire() as conn:
            assert (
                await conn.fetchval("SELECT COUNT(DISTINCT real_job_id) FROM jobs")
                == EXPECTED_SEPARATE_REAL_JOBS
            )
            assert (
                await conn.fetchval("SELECT COUNT(*) FROM real_job_review_cases") == 1
            )
    finally:
        await store.close()


async def test_legacy_source_reconciliation_keeps_status_history(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        async with store._get_pool().acquire() as conn:
            source_id = await conn.fetchval(
                "INSERT INTO jobs("
                "platform,canonical_id,url,title,company,location,discovered_at) "
                "VALUES('linkedin','legacy','https://example.test/legacy','Engineer',"
                "'Example','Detroit',now()) RETURNING id"
            )
            history_before = await conn.fetchval(
                "SELECT COUNT(*) FROM job_status_history WHERE job_id=$1", source_id
            )
        assert await store.reconcile_real_jobs() == 0
        assert await store.reconcile_real_jobs() == 0
        async with store._get_pool().acquire() as conn:
            assert await conn.fetchval(
                "SELECT real_job_id FROM jobs WHERE id=$1", source_id
            )
            assert await conn.fetchval("SELECT COUNT(*) FROM real_jobs") == 1
            assert await conn.fetchval("SELECT COUNT(*) FROM real_job_identifiers") == 1
            assert (
                await conn.fetchval(
                    "SELECT COUNT(*) FROM job_status_history WHERE job_id=$1", source_id
                )
                == history_before
            )
    finally:
        await store.close()
