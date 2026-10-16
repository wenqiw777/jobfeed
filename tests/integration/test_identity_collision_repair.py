"""Conflicting ownership must not leave duplicate parents eligible for scoring."""

import json
from dataclasses import replace

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from tests.integration.test_real_job_resolver_sqlite import _parent
from tests.support.sqlite_jobs_evaluations import make_job, open_sqlite_store

ATS = "https://example.wd1.myworkdayjobs.com/en-US/jobs/job/US/Engineer_R123"


async def test_bridge_of_two_compatible_identifier_owners_merges(tmp_path):
    lifecycle, store = await open_sqlite_store(tmp_path / "bridge.db")
    try:
        a = await store.save_job(replace(make_job("a", jd_text="first"), url=ATS))
        old = replace(make_job("b", jd_text="second"), platform="jobright")
        b = await store.save_job(old)
        await store.save_job(replace(old, apply_url=ATS))
        assert await _parent(lifecycle, a.job_id) == await _parent(lifecycle, b.job_id)
    finally:
        await lifecycle.close()


async def test_conflicting_statuses_are_held_not_silently_recommended(tmp_path):
    lifecycle, store = await open_sqlite_store(tmp_path / "hold.db")
    try:
        a = await store.save_job(replace(make_job("a"), url=ATS))
        bjob = replace(make_job("b", jd_text="second"), platform="jobright")
        b = await store.save_job(bjob)
        async with lifecycle.connection() as c:
            await c.execute(
                "update real_job_status set status='ghosted' where real_job_id=?",
                (await _parent(lifecycle, a.job_id),),
            )
            await c.execute(
                "update real_job_status set status='archived' where real_job_id=?",
                (await _parent(lifecycle, b.job_id),),
            )
        await store.save_job(replace(bjob, apply_url=ATS))
        assert await _parent(lifecycle, a.job_id) != await _parent(lifecycle, b.job_id)
        async with lifecycle.connection() as c:
            states = await (
                await c.execute("select identity_review_state from real_jobs")
            ).fetchall()
        assert all(r[0] == "identity_conflict" for r in states)
    finally:
        await lifecycle.close()


async def test_distinct_workday_suffix_stays_separate_until_verified(tmp_path):

    store = SQLiteStore(tmp_path / "alias.db")
    await store.connect()
    try:
        root = replace(make_job("base"), url=ATS)
        variant = replace(make_job("variant"), url=ATS + "-1", apply_url=ATS + "-1")
        a = await store.save_job(root)
        b = await store.save_job(variant)
        assert await _parent(store._lifecycle, a.job_id) != await _parent(
            store._lifecycle, b.job_id
        )
        receipt = json.dumps(
            {"status": "resolved", "ats_url": ATS + "-1", "workday_requisition": "R123"}
        )
        await store.record_application_identity(
            job_id=b.job_id,
            expected_apply_url=ATS + "-1",
            ats_url=ATS + "-1",
            state_key="test-alias",
            state_value=receipt,
        )
        assert await _parent(store._lifecycle, a.job_id) == await _parent(
            store._lifecycle, b.job_id
        )
    finally:
        await store.close()


@pytest.mark.parametrize("conflict", [False, True])
async def test_three_way_bridge_joins_consistent_source_and_ats_owners(
    tmp_path, conflict
):
    lifecycle, store = await open_sqlite_store(tmp_path / "three.db")
    try:
        a = await store.save_job(replace(make_job("a", jd_text="first"), url=ATS))
        b = await store.save_job(
            replace(make_job("legacy", jd_text="second"), platform="speedyapply")
        )
        bparent = await _parent(lifecycle, b.job_id)
        async with lifecycle.connection() as c:
            await c.execute(
                "insert into real_job_identifiers(real_job_id,provider,scope,native_id,"
                "evidence_job_id,observed_url) values (?,'jobright','','third',?,?)",
                (bparent, int(b.job_id), "https://jobright.ai/jobs/info/third"),
            )
        if conflict:
            async with lifecycle.connection() as c:
                await c.execute(
                    "UPDATE real_job_status SET status='archived' WHERE real_job_id=?",
                    (bparent,),
                )
                await c.execute(
                    "UPDATE real_job_status SET status='ghosted' WHERE real_job_id=?",
                    (await _parent(lifecycle, a.job_id),),
                )
        bridge = await store.save_job(
            replace(make_job("third", jd_text="third"), platform="jobright", url=ATS)
        )
        if conflict:
            async with lifecycle.connection() as c:
                states = await (
                    await c.execute("select identity_review_state from real_jobs")
                ).fetchall()
            assert all(r[0] == "identity_conflict" for r in states)
            assert len(states) == len([a, b, bridge])
        else:
            assert (
                await _parent(lifecycle, a.job_id)
                == await _parent(lifecycle, b.job_id)
                == await _parent(lifecycle, bridge.job_id)
            )
    finally:
        await lifecycle.close()


async def test_conflicting_scoped_requisitions_are_not_merged_by_bridge(tmp_path):
    lifecycle, store = await open_sqlite_store(tmp_path / "different.db")
    try:
        a = await store.save_job(replace(make_job("a"), url=ATS))
        b = await store.save_job(
            replace(make_job("b"), url=ATS.replace("R123", "R999"))
        )
        bridge = await store.save_job(
            replace(make_job("bridge"), url=ATS, apply_url=ATS.replace("R123", "R999"))
        )
        assert await _parent(lifecycle, a.job_id) != await _parent(lifecycle, b.job_id)
        assert await _parent(lifecycle, bridge.job_id) not in [
            await _parent(lifecycle, a.job_id),
            await _parent(lifecycle, b.job_id),
        ]
    finally:
        await lifecycle.close()


async def test_previous_application_suppresses_results_even_when_scored_again(tmp_path):
    store = SQLiteStore(tmp_path / "applied.db")
    await store.connect()
    try:
        saved = await store.save_job(replace(make_job("applied"), url=ATS))
        parent = await _parent(store._lifecycle, saved.job_id)
        async with store._lifecycle.connection() as c:
            await c.execute(
                "insert into real_job_applications"
                "(real_job_id,source_job_id,applied_at) "
                "values (?,?,?)",
                (parent, int(saved.job_id), "2026-09-07T00:00:00Z"),
            )
            await c.execute(
                "update real_job_status set status='scored' where real_job_id=?",
                (parent,),
            )
        results = await store.query_real_jobs_view(decision="results")
        assert results["total"] == 0
    finally:
        await store.close()
