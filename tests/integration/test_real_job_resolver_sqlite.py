"""Real-job identity resolution on disposable SQLite stores."""

from dataclasses import replace
from pathlib import Path

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.adapters.store.sqlite_schema import migrate_real_jobs_schema
from tests.support.sqlite_jobs_evaluations import make_job, open_sqlite_store

EXPECTED_EXACT_BACKFILL_SOURCES = 2

_JD = "Requirements: Python, distributed systems, and API ownership. " * 12
_ATS = "https://qualcomm.eightfold.ai/careers?pid=446721162271"


async def _parent(lifecycle, source_id: str) -> int:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "SELECT real_job_id FROM jobs WHERE id=?", (int(source_id),)
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row is not None
        return int(row[0])


async def test_strict_content_joins_cross_source_and_keeps_distinct_team(
    tmp_path: Path,
) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "resolver.db")
    try:
        first = await store.save_job(
            replace(
                make_job("first", jd_text=_JD),
                platform="linkedin",
                company="Qualcomm",
                title="Backend SWE",
            )
        )
        second = await store.save_job(
            replace(
                make_job("second", jd_text=_JD),
                platform="jobright",
                company="Qualcomm",
                title="Backend SWE",
            )
        )
        different = await store.save_job(
            replace(
                make_job(
                    "third", jd_text=_JD.replace("distributed systems", "mobile apps")
                ),
                platform="speedyapply",
                company="Qualcomm",
                title="Backend SWE",
            )
        )
        assert await _parent(lifecycle, first.job_id) == await _parent(
            lifecycle, second.job_id
        )
        assert await _parent(lifecycle, different.job_id) != await _parent(
            lifecycle, first.job_id
        )
    finally:
        await lifecycle.close()


async def test_explicit_status_conflict_creates_review_case(tmp_path: Path) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "conflict.db")
    try:
        first = await store.save_job(
            replace(
                make_job("one", jd_text=_JD),
                platform="linkedin",
                company="Qualcomm",
                title="Backend SWE",
            )
        )
        second_job = replace(
            make_job("two", jd_text="Different description"),
            platform="jobright",
            company="Qualcomm",
            title="Backend SWE",
        )
        second = await store.save_job(second_job)
        async with lifecycle.connection() as connection:
            await connection.execute(
                "UPDATE job_status SET status='applied' WHERE job_id=?",
                (int(first.job_id),),
            )
            await connection.execute(
                "UPDATE job_status SET status='ignored' WHERE job_id=?",
                (int(second.job_id),),
            )
        await store.save_job(replace(second_job, jd_text=_JD))
        assert await _parent(lifecycle, first.job_id) != await _parent(
            lifecycle, second.job_id
        )
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM real_job_review_cases"
            )
            assert (await cursor.fetchone())[0] == 1
            await cursor.close()
    finally:
        await lifecycle.close()


async def test_shared_scoped_ats_id_joins_despite_different_jd(tmp_path: Path) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "same-ats.db")
    try:
        first = await store.save_job(
            replace(
                make_job("li-1", jd_text="LinkedIn extract"),
                platform="linkedin",
                company="Qualcomm",
                title="Backend SWE",
                apply_url=_ATS,
            )
        )
        second = await store.save_job(
            replace(
                make_job("jr-1", jd_text="Jobright extract"),
                platform="jobright",
                company="Qualcomm",
                title="Backend SWE",
                url=_ATS,
            )
        )
        assert await _parent(lifecycle, first.job_id) == await _parent(
            lifecycle, second.job_id
        )
    finally:
        await lifecycle.close()


async def test_shared_ats_with_conflicting_complete_jds_holds_without_score(
    tmp_path: Path,
) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "requirements-conflict.db")
    try:
        first = await store.save_job(
            replace(
                make_job("li-requirements", jd_text=_JD),
                platform="linkedin",
                company="Qualcomm",
                title="Backend SWE",
                apply_url=_ATS,
            )
        )
        second = await store.save_job(
            replace(
                make_job(
                    "jr-requirements",
                    jd_text=_JD.replace("distributed systems", "mobile apps"),
                ),
                platform="jobright",
                company="Qualcomm",
                title="Backend SWE",
                url=_ATS,
            )
        )
        parent = await _parent(lifecycle, first.job_id)
        assert parent == await _parent(lifecycle, second.job_id)
        async with lifecycle.connection() as connection:
            state = await (
                await connection.execute(
                    "SELECT identity_review_state FROM real_jobs WHERE id=?", (parent,)
                )
            ).fetchone()
            cases = await (
                await connection.execute(
                    "SELECT left_job_id,right_job_id,reason FROM real_job_review_cases "
                    "WHERE left_real_job_id=? AND right_real_job_id=?",
                    (parent, parent),
                )
            ).fetchall()
            evaluation = await (
                await connection.execute(
                    "SELECT 1 FROM real_job_evaluations WHERE real_job_id=?", (parent,)
                )
            ).fetchone()
        assert state[0] == "requirements_conflict"
        assert cases == [
            (int(first.job_id), int(second.job_id), "requirements_conflict")
        ]
        assert evaluation is None
    finally:
        await lifecycle.close()


async def test_distinct_ats_requisitions_do_not_join_even_with_same_jd(
    tmp_path: Path,
) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "other-ats.db")
    try:
        first = await store.save_job(
            replace(
                make_job("jr-1", jd_text=_JD),
                platform="jobright",
                company="Qualcomm",
                title="Backend SWE",
                url=_ATS,
            )
        )
        bridge = await store.save_job(
            replace(
                make_job("li-bridge", jd_text=_JD),
                platform="linkedin",
                company="Qualcomm",
                title="Backend SWE",
                url="https://www.linkedin.com/jobs/view/4469009999/",
            )
        )
        second = await store.save_job(
            replace(
                make_job("jr-2", jd_text=_JD),
                platform="speedyapply",
                company="Qualcomm",
                title="Backend SWE",
                url="https://qualcomm.eightfold.ai/careers?pid=446721162272",
            )
        )
        assert await _parent(lifecycle, first.job_id) == await _parent(
            lifecycle, bridge.job_id
        )
        assert await _parent(lifecycle, first.job_id) != await _parent(
            lifecycle, second.job_id
        )
    finally:
        await lifecycle.close()


async def test_explicit_backfill_joins_only_observed_exact_ids(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "backfill.db")
    await store.connect()
    try:
        async with store._lifecycle.connection() as connection:
            for platform, native in (("linkedin", "li-old"), ("jobright", "jr-old")):
                await connection.execute(
                    "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
                    "discovered_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        platform,
                        native,
                        _ATS,
                        "Backend SWE",
                        "Qualcomm",
                        "San Diego, CA",
                        "2026-09-24T00:00:00.000000Z",
                    ),
                )
        async with store._lifecycle.connection() as connection:
            await migrate_real_jobs_schema(connection)
        last, processed = await store.backfill_real_job_identifiers(limit=10)
        assert processed == EXPECTED_EXACT_BACKFILL_SOURCES
        assert last > 0
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT COUNT(DISTINCT real_job_id) FROM jobs"
            )
            assert (await cursor.fetchone())[0] == 1
            await cursor.close()
    finally:
        await store.close()


async def test_exact_id_backfill_holds_divergent_full_descriptions(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "backfill-conflict.db")
    await store.connect()
    try:
        async with store._lifecycle.connection() as connection:
            for platform, native, body in (
                ("linkedin", "li-old", _JD),
                (
                    "jobright",
                    "jr-old",
                    _JD.replace("distributed systems", "mobile apps"),
                ),
            ):
                await connection.execute(
                    "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
                    "jd_text,jd_quality,discovered_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        platform,
                        native,
                        _ATS,
                        "Backend SWE",
                        "Qualcomm",
                        "San Diego, CA",
                        body,
                        "full",
                        "2026-09-24T00:00:00Z",
                    ),
                )
        async with store._lifecycle.connection() as connection:
            await migrate_real_jobs_schema(connection)
        assert (await store.backfill_real_job_identifiers(limit=10))[
            1
        ] == EXPECTED_EXACT_BACKFILL_SOURCES
        async with store._lifecycle.connection() as connection:
            parents = await (
                await connection.execute("SELECT DISTINCT real_job_id FROM jobs")
            ).fetchall()
            state = await (
                await connection.execute(
                    "SELECT identity_review_state FROM real_jobs WHERE id=?",
                    (parents[0][0],),
                )
            ).fetchone()
            cases = await (
                await connection.execute("SELECT reason FROM real_job_review_cases")
            ).fetchall()
        assert len(parents) == 1
        assert state[0] == "requirements_conflict"
        assert cases == [("requirements_conflict",)]
    finally:
        await store.close()


async def test_observed_alias_reviews_do_not_freeze_verified_third_source(
    tmp_path: Path,
) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "observed-triples.db")
    try:
        southwest_url = (
            "https://careers.southwestair.com/us/en/job/"
            "SOUTUSR202672886ENUSEXTERNAL/Software-Engineer"
        )
        sw_alias = await store.save_job(
            replace(
                make_job("sw-alias", jd_text=_JD),
                platform="jobright",
                url=southwest_url,
                company="Southwest Airline Career Page",
                title="Associate Software Engineer - Direct College Hire",
                location="United States",
            )
        )
        sw_official_job = replace(
            make_job("sw-official", jd_text="Incomplete early extract"),
            platform="jobright",
            url=southwest_url + "?utm_source=feed",
            company="Southwest Airlines",
            title="Associate Software Engineer - Direct College Hire",
            location="United States",
        )
        sw_official = await store.save_job(sw_official_job)
        sw_linkedin = await store.save_job(
            replace(
                make_job("sw-linkedin", jd_text=_JD),
                platform="linkedin",
                url="https://www.linkedin.com/jobs/view/4469044914/",
                company="Southwest Airlines",
                title="Associate Software Engineer - Direct College Hire",
                location="Dallas, TX",
            )
        )
        await store.save_job(replace(sw_official_job, jd_text=_JD))
        assert (
            len(
                {
                    await _parent(lifecycle, x.job_id)
                    for x in (sw_alias, sw_official, sw_linkedin)
                }
            )
            == 1
        )

        jr_url = "https://jobright.ai/jobs/info/6ab1a0eb191d8c340dbdbf57"
        quick = await store.save_job(
            replace(
                make_job("sa-qualcomm", jd_text="Short extract"),
                platform="speedyapply",
                url=jr_url,
                company="Qualcomm",
                title="#Backend Software Engineer",
                location="San Diego, CA, United States",
            )
        )
        ats = await store.save_job(
            replace(
                make_job("6ab1a0eb191d8c340dbdbf57", jd_text=_JD),
                platform="jobright",
                url=_ATS,
                company="Qualcomm",
                title="#Backend Software Engineer",
                location="San Diego, CA",
            )
        )
        linkedin = await store.save_job(
            replace(
                make_job("li-qualcomm", jd_text=_JD),
                platform="linkedin",
                url="https://www.linkedin.com/jobs/view/4469009469/",
                company="Qualcomm",
                title="#Backend Software Engineer",
                location="San Diego, CA",
            )
        )
        assert (
            len({await _parent(lifecycle, x.job_id) for x in (quick, ats, linkedin)})
            == 1
        )
    finally:
        await lifecycle.close()


async def test_unrelated_review_rehomes_when_its_parent_loses_merge(
    tmp_path: Path,
) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "review-rehome.db")
    try:
        first_job = replace(
            make_job("first", jd_text="Initial unrelated description"),
            platform="linkedin",
            company="Qualcomm",
            title="Backend SWE",
        )
        first = await store.save_job(first_job)
        second = await store.save_job(
            replace(
                make_job("second", jd_text=_JD),
                platform="jobright",
                company="Qualcomm",
                title="Backend SWE",
            )
        )
        disputed = await store.save_job(
            replace(
                make_job("disputed", jd_text="Other team"),
                platform="speedyapply",
                company="Qualcomm",
                title="Frontend SWE",
            )
        )
        first_parent = await _parent(lifecycle, first.job_id)
        second_parent = await _parent(lifecycle, second.job_id)
        disputed_parent = await _parent(lifecycle, disputed.job_id)
        async with lifecycle.connection() as connection:
            await connection.execute(
                "INSERT INTO real_job_review_cases("
                "left_real_job_id,right_real_job_id,left_job_id,right_job_id,reason) "
                "VALUES(?,?,?,?,?)",
                (
                    second_parent,
                    disputed_parent,
                    int(second.job_id),
                    int(disputed.job_id),
                    "conflicting_role_facts",
                ),
            )
        await store.save_job(replace(first_job, jd_text=_JD))
        assert (
            await _parent(lifecycle, first.job_id)
            == await _parent(lifecycle, second.job_id)
            == first_parent
        )
        assert await _parent(lifecycle, disputed.job_id) == disputed_parent
        async with lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT left_real_job_id,right_real_job_id FROM real_job_review_cases"
            )
            assert tuple(await cursor.fetchone()) == (first_parent, disputed_parent)
            await cursor.close()
    finally:
        await lifecycle.close()


async def test_same_ashby_requisition_merges_source_title_and_location_variants(
    tmp_path: Path,
) -> None:
    lifecycle, store = await open_sqlite_store(tmp_path / "quora.db")
    url = "https://jobs.ashbyhq.com/quora/cf34f80e-fe5c-454d-bc9a-4c59993ffda0/application"
    try:
        first = await store.save_job(
            replace(
                make_job("quora-jobright", jd_text=_JD),
                platform="jobright",
                url=url,
                company="Quora",
                title=(
                    "Software Engineer New Grad, Machine Learning Platform "
                    "- Quora (Remote)"
                ),
                location="United States",
            )
        )
        second = await store.save_job(
            replace(
                make_job("quora-speedy", jd_text=_JD),
                platform="speedyapply",
                url=url + "?embed=true&utm_source=Simplify",
                company="Quora",
                title="Software Engineer New Grad - Machine Learning Platform",
                location="Remote in USA Remote in Canada",
            )
        )
        assert await _parent(lifecycle, first.job_id) == await _parent(
            lifecycle, second.job_id
        )
        async with lifecycle.connection() as connection:
            assert (
                await (await connection.execute("SELECT COUNT(*) FROM jobs")).fetchone()
            )[0] == EXPECTED_EXACT_BACKFILL_SOURCES
            assert (
                await (
                    await connection.execute(
                        "SELECT COUNT(*) FROM real_job_review_cases"
                    )
                ).fetchone()
            )[0] == 0
    finally:
        await lifecycle.close()
