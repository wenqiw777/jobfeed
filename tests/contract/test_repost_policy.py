"""Reposts retain identity and scores but cannot be claimed for model work."""

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.adapters.store.sqlite_claims_runs import SqliteClaimsRuns
from jobfeed.domain.models_views import JobsViewQuery
from jobfeed.services._jobs_view_sort import LIBRARY_SORT_KEYS
from tests.support.sqlite_jobs_evaluations import (
    FIXED_NOW,
    make_job,
    open_sqlite_store,
    stage_a,
)


async def test_repost_rescan_preserves_score_and_blocks_both_claim_paths(tmp_path):
    lifecycle, store = await open_sqlite_store(tmp_path / "repost.db")
    claims = SqliteClaimsRuns(lifecycle, clock=lambda: FIXED_NOW)
    try:
        job = make_job("repost")
        saved = await store.save_job(job)
        await store.save_stage_a(saved.job_id, stage_a())
        result = await store.save_job(
            replace(
                job,
                is_repost=True,
                repost_evidence="Reposted 1 day ago",
                repost_observed_at=FIXED_NOW,
                discovered_at=FIXED_NOW + timedelta(days=1),
            )
        )
        assert not result.inserted
        await store.save_job(job)  # Missing observation cannot erase a positive.
        loaded = await store.get_job(saved.job_id)
        assert loaded.is_repost is True
        assert loaded.repost_evidence == "Reposted 1 day ago"
        assert loaded.discovered_at == FIXED_NOW
        assert (
            await store.get_evaluation(saved.job_id)
        ).stage_a.score == stage_a().score
        assert await claims.load_gate_candidates(corpus="all", limit=100) == []
        assert (
            await claims.claim_stage_a_by_ids([saved.job_id], corpus="all", limit=100)
            == []
        )
        assert await claims.claim_pending_stage_b(limit=100) == []
    finally:
        await lifecycle.close()


async def test_unknown_repost_remains_eligible(tmp_path):
    lifecycle, store = await open_sqlite_store(tmp_path / "unknown.db")
    claims = SqliteClaimsRuns(lifecycle, clock=lambda: FIXED_NOW)
    try:
        await store.save_job(make_job("unknown"))
        assert len(await claims.load_gate_candidates(corpus="unrated", limit=100)) == 1
    finally:
        await lifecycle.close()


async def test_sql_order_matches_memory_before_page_boundaries(tmp_path):
    store = SQLiteStore(tmp_path / "pages.db")
    await store.connect()
    try:
        for index, (score, repost, at) in enumerate(
            [
                (99, True, "2026-09-20T03:59:00+00:00"),
                (91, None, "2026-09-19T12:00:00+00:00"),
                (89, False, "2026-09-20T04:01:00+00:00"),
                (100, False, "2026-09-19T11:00:00+00:00"),
                (None, None, "2026-11-01T06:30:00+00:00"),
                (90, True, "2026-11-01T05:30:00+00:00"),
            ]
        ):
            saved = await store.save_job(
                replace(
                    make_job(str(index)),
                    is_repost=repost,
                    discovered_at=datetime.fromisoformat(at),
                )
            )
            if score is not None:
                await store.save_stage_a(saved.job_id, stage_a(score))
        rows = (await store.query_jobs_view(JobsViewQuery(tab="all", limit=100))).rows
        for sort in [
            "triage_posted_asc",
            "triage_posted_desc",
            "triage_score_asc",
            "triage_score_desc",
        ]:
            expected = [r.job.id for r in sorted(rows, key=LIBRARY_SORT_KEYS[sort])]
            actual = []
            for offset in range(0, 6, 2):
                page = await store.query_jobs_view(
                    JobsViewQuery(tab="all", sort=sort, limit=2, offset=offset)
                )
                actual.extend(r.job.id for r in page.rows)
            assert actual == expected
    finally:
        await store.close()


async def test_existing_database_adds_unknown_repost_columns(tmp_path):
    path = tmp_path / "upgrade.db"
    store = SQLiteStore(path)
    await store.connect()
    saved = await store.save_job(make_job("old"))
    await store.close()
    with sqlite3.connect(path) as db:
        for column in ("is_repost", "repost_evidence", "repost_observed_at"):
            db.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
    await store.connect()
    try:
        assert (await store.get_job(saved.job_id)).is_repost is None
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT is_repost,repost_evidence,repost_observed_at FROM jobs"
            ).fetchone() == (None, None, None)
    finally:
        await store.close()
