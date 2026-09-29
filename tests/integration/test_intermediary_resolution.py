"""Real SQLite attribution preserves the official parent and scored state."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.observability import get_logger
from jobfeed.services.intermediary_resolution import IntermediaryResolver
from jobfeed.services.scan import ScanService
from tests.unit.test_intermediary_resolution import official, posting

EXPECTED_SOURCES = 3


@pytest.mark.parametrize("source_first", [False, True])
async def test_internal_first_reuses_official_parent(tmp_path, source_first):
    store = SQLiteStore(tmp_path / "demo.sqlite")
    await store.connect()
    try:
        source = posting(id=None)
        if source_first:
            await store.save_job(source)
        saved = await store.save_job(official(id=None))
        async with store._lifecycle.connection() as db:
            target_id = (
                await (
                    await db.execute(
                        "SELECT real_job_id FROM jobs WHERE id=?", (saved.job_id,)
                    )
                ).fetchone()
            )[0]
        search = AsyncMock(return_value=[])
        resolver = IntermediaryResolver(store, search=search)
        await resolver.resolve([source], run_id="demo")
        search.assert_not_called()
        async with store._lifecycle.connection() as db:
            rows = await (await db.execute("SELECT real_job_id FROM jobs")).fetchall()
            assert {r[0] for r in rows} == {target_id}
            assert (
                await (
                    await db.execute(
                        "SELECT representative_job_id FROM real_jobs WHERE id=?",
                        (target_id,),
                    )
                ).fetchone()
            )[0] == int(saved.job_id)
            plan = await (
                await db.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM jobs "
                    "WHERE title_norm=? ORDER BY id LIMIT 201",
                    ("software engineer",),
                )
            ).fetchall()
            assert any("idx_jobs_title_lookup" in r[3] for r in plan)
    finally:
        await store.close()


async def test_external_fallback_and_negative_cache(tmp_path):
    store = SQLiteStore(tmp_path / "demo.sqlite")
    await store.connect()
    try:
        search = AsyncMock(return_value=[])
        resolver = IntermediaryResolver(store, search=search)
        await resolver.resolve([posting(id=None)], run_id="one")
        await resolver.resolve([posting(id=None)], run_id="two")
        assert search.await_count == 1
        assert await store.claim_real_job_stage_a_by_ids(["1"]) == []
        search.reset_mock()
        other = replace(posting(id=None), canonical_id="other")
        search.return_value = [official(id=None)]
        await resolver.resolve([other], run_id="three")
        assert search.await_count == 1
        assert len(await store.list_jobs()) == EXPECTED_SOURCES
    finally:
        await store.close()


async def test_ambiguity_does_not_merge_or_search(tmp_path):
    store = SQLiteStore(tmp_path / "demo.sqlite")
    await store.connect()
    try:
        await store.save_job(official(id=None))
        await store.save_job(
            official(
                id=None,
                canonical_id="other",
                url="https://boards.greenhouse.io/acme/jobs/124",
            )
        )
        search = AsyncMock(return_value=[])
        await IntermediaryResolver(store, search=search).resolve(
            [posting(id=None)], run_id="demo"
        )
        search.assert_not_called()
        async with store._lifecycle.connection() as db:
            assert (
                await (await db.execute("SELECT count(*) FROM real_jobs")).fetchone()
            )[0] == EXPECTED_SOURCES
            assert (
                await (
                    await db.execute(
                        "SELECT count(*) FROM real_jobs "
                        "WHERE identity_review_state!='clear'"
                    )
                ).fetchone()
            )[0] == 0
    finally:
        await store.close()


async def test_completed_official_score_survives_link_and_unresolved_is_hidden(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "demo.sqlite")
    await store.connect()
    try:
        source = posting(id=None)
        await store.save_job(source)
        target = await store.save_job(official(id=None))
        async with store._lifecycle.connection() as db:
            parent = (
                await (
                    await db.execute(
                        "SELECT real_job_id FROM jobs WHERE id=?", (target.job_id,)
                    )
                ).fetchone()
            )[0]
        await store.claim_real_job_stage_a_by_ids([str(parent)])
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_job_evaluations SET stage_a_status='completed',"
                "stage_a_score=88,stage_b_status='completed',"
                "stage_b_verdict='apply' WHERE real_job_id=?",
                (parent,),
            )
            await db.commit()
            before = await (
                await db.execute(
                    "SELECT * FROM real_job_evaluations WHERE real_job_id=?", (parent,)
                )
            ).fetchone()
        await IntermediaryResolver(store).resolve([source], run_id="demo")
        async with store._lifecycle.connection() as db:
            after = await (
                await db.execute(
                    "SELECT * FROM real_job_evaluations WHERE real_job_id=?", (parent,)
                )
            ).fetchone()
            assert tuple(before) == tuple(after)
        assert await store.claim_real_job_stage_a_by_ids([str(parent)]) == []
        unresolved = posting(id=None, canonical_id="unresolved", title="Unrelated Role")
        await store.save_job(unresolved)
        view = await store.query_real_jobs_view(decision="results")
        assert view["total"] == 1
        assert len(await store.list_jobs()) == EXPECTED_SOURCES
    finally:
        await store.close()


async def test_search_budget_timeout_and_error_are_durable(tmp_path):
    store = SQLiteStore(tmp_path / "demo.sqlite")
    await store.connect()
    try:
        search = AsyncMock(side_effect=RuntimeError("search unavailable"))
        sources = [
            posting(id=None, canonical_id=str(i), title=f"Engineer {i}")
            for i in range(4)
        ]
        await IntermediaryResolver(store, search=search, max_searches=1).resolve(
            sources, run_id="demo"
        )
        assert search.await_count == 1

        first = json.loads(await store.get_state("intermediary-resolution:jobright:0"))
        assert first["outcome"] == "search_failed"
        assert first["error"] == "search unavailable"
        later = json.loads(await store.get_state("intermediary-resolution:jobright:1"))
        assert later["outcome"] == "search_deferred"
    finally:
        await store.close()


async def test_scan_resolves_only_after_both_sources_are_saved(tmp_path):

    class Source:
        def __init__(self, jobs):
            self.jobs = jobs

        async def fetch_jobs(self, _config):
            return self.jobs

    store = SQLiteStore(tmp_path / "scan.sqlite")
    await store.connect()
    try:
        search = AsyncMock(return_value=[])
        scan = ScanService(
            store, get_logger(), intermediary=IntermediaryResolver(store, search=search)
        )
        run = await scan.run(
            [
                ("jobright", Source([posting(id=None)]), {}),
                ("ats", Source([official(id=None)]), {}),
            ]
        )
        search.assert_not_called()
        assert run.errors == 0
        assert run.scan_progress["intermediary"]["phase"] == "completed"
        async with store._lifecycle.connection() as db:
            assert (
                await (await db.execute("SELECT count(*) FROM real_jobs")).fetchone()
            )[0] == 1
    finally:
        await store.close()


async def test_lost_lease_prevents_external_official_write(tmp_path):
    store = SQLiteStore(tmp_path / "demo.sqlite")
    await store.connect()
    lost = False

    async def search(_source, _run):
        nonlocal lost
        lost = True
        return [official(id=None)]

    def check():
        if lost:
            raise RunLeaseLostError("lost")

    try:
        with pytest.raises(RunLeaseLostError):
            await IntermediaryResolver(store, search=search).resolve(
                [posting(id=None)], run_id="demo", ensure_active=check
            )
        rows = await store.list_jobs()
        assert len(rows) == 1
        assert rows[0].company == "Dice"
    finally:
        await store.close()


async def test_additive_index_installs_without_changing_existing_sources(tmp_path):
    path = tmp_path / "demo.sqlite"
    store = SQLiteStore(path)
    await store.connect()
    saved = await store.save_job(official(id=None))
    async with store._lifecycle.connection() as db:
        await db.execute("DROP INDEX idx_jobs_title_lookup")
        await db.commit()
    await store.close()
    await store.connect()
    try:
        assert (await store.get_job(saved.job_id)).jd_text == official().jd_text
        async with store._lifecycle.connection() as db:
            assert (await (await db.execute("PRAGMA quick_check")).fetchone())[
                0
            ] == "ok"
            assert await (
                await db.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name='idx_jobs_title_lookup'"
                )
            ).fetchone()
    finally:
        await store.close()


async def test_results_domain_filter_does_not_match_a_url_path(tmp_path):
    store = SQLiteStore(tmp_path / "domains.sqlite")
    await store.connect()
    try:
        await store.save_job(
            official(id=None, url="https://safe.example/path/.dice.com/job")
        )
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
        await store.save_job(posting(id=None, company="Acme", url="https://dice.com"))
        assert (await store.query_real_jobs_view(decision="results"))["total"] == 1
    finally:
        await store.close()


async def test_conflicting_user_decisions_are_not_reported_as_a_match(tmp_path):
    store = SQLiteStore(tmp_path / "conflict.sqlite")
    await store.connect()
    try:
        source = posting(id=None)
        saved = await store.save_job(source)
        target = await store.save_job(official(id=None))
        parents = await store.resolve_real_job_ids([saved.job_id, target.job_id])
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE real_job_status SET status='ignored' WHERE real_job_id=?",
                (parents[0],),
            )
            await db.execute(
                "UPDATE real_job_status SET status='applied' WHERE real_job_id=?",
                (parents[1],),
            )
            await db.commit()
        await IntermediaryResolver(store).resolve([source], run_id="demo")
        result = json.loads(
            await store.get_state("intermediary-resolution:jobright:one")
        )
        assert result["outcome"] == "identity_conflict"
        assert (
            await store.resolve_real_job_ids([saved.job_id, target.job_id]) == parents
        )
    finally:
        await store.close()
