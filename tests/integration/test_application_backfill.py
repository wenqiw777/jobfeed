"""Historical application evidence reuses fenced resolution without source edits."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.application_route import ApplicationRouteOutcome
from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.models import PipelineRun
from jobfeed.services.application_backfill import ApplicationBackfillService
from jobfeed.services.run_orchestration import RunLeaseSession
from tests.support.sqlite_jobs_evaluations import FIXED_NOW, make_job

_ATS = "https://boards.greenhouse.io/example/jobs/123456"
_EXPECTED_CANDIDATES = 8


@pytest.fixture
async def store(tmp_path):
    value = SQLiteStore(tmp_path / "application-backfill.sqlite")
    await value.connect()
    try:
        yield value
    finally:
        await value.close()


async def _lease(store):
    now = datetime.now(UTC)
    run = PipelineRun(
        run_id="019b1251-a26e-7466-a744-28b881a19cd5",
        source="application-backfill",
        started_at=now,
    )
    owner = "019b1251-a26e-7466-a744-28b881a19cd6"
    generation = await store.start_run_with_lease(
        run, kind="scan", owner_id=owner, now=now
    )
    assert generation is not None
    return RunLeaseSession(
        run=run,
        kind="scan",
        owner_id=owner,
        generation=generation,
        _store=store,
        _clock=lambda: datetime.now(UTC),
        _heartbeat_interval_seconds=30,
    )


async def _save(store, key, *, apply_url=None, platform="linkedin"):
    return (
        await store.save_job(
            replace(
                make_job(key, jd_text=f"Original description {key}"),
                platform=platform,
                url=f"https://www.linkedin.com/jobs/view/{key}/",
                apply_url=apply_url,
            )
        )
    ).job_id


async def test_candidate_snapshot_includes_missing_jd_and_orders_apply_first(store):
    no_apply = []
    platforms = (
        "linkedin",
        "linkedin_guest",
        "linkedin_jobspy",
        "jobright",
        "speedyapply",
        "indeed",
        "official_search",
    )
    for index, platform in enumerate(platforms):
        saved = await store.save_job(
            replace(
                make_job(
                    platform,
                    discovered_at=FIXED_NOW + timedelta(minutes=index),
                    jd_text=None,
                ),
                platform=platform,
            )
        )
        no_apply.append(saved.job_id)
    applied = await store.save_job(
        replace(make_job("applied"), apply_url="https://careers.example.test/job/1")
    )
    await store.save_job(make_job("unrelated"))
    await store.save_job(
        replace(make_job("closed", closed_at=FIXED_NOW), platform="linkedin")
    )
    snapshot = await store.list_application_backfill_ids()
    assert len(snapshot) == _EXPECTED_CANDIDATES
    assert snapshot == [applied.job_id, *reversed(no_apply)]
    await _save(store, "new")
    assert snapshot == [applied.job_id, *reversed(no_apply)]
    assert await store.list_application_backfill_ids(
        since=FIXED_NOW + timedelta(minutes=5)
    ) == list(reversed(no_apply[5:]))


async def test_recent_backfill_uses_posting_age_and_excludes_closed_parent(store):
    cutoff = FIXED_NOW - timedelta(days=30)
    cases = {
        "old-posting-new-discovery": (FIXED_NOW, cutoff - timedelta(days=1), None),
        "posting-after-first-discovery": (cutoff - timedelta(days=5), FIXED_NOW, None),
        "recent-posting": (FIXED_NOW, FIXED_NOW - timedelta(days=2), None),
        "unknown-recent": (FIXED_NOW, None, None),
        "unknown-old": (cutoff - timedelta(days=1), None, None),
        "boundary": (FIXED_NOW, cutoff, None),
        "source-closed": (FIXED_NOW, FIXED_NOW, FIXED_NOW),
        "parent-closed": (FIXED_NOW, FIXED_NOW, None),
        "canonical-old": (FIXED_NOW, FIXED_NOW, None),
    }
    ids = {}
    for key, (discovered, posted, closed) in cases.items():
        saved = await store.save_job(
            replace(
                make_job(key, discovered_at=discovered, jd_text=f"Description {key}"),
                platform="linkedin",
                posted_at=posted,
                closed_at=closed,
            )
        )
        ids[key] = saved.job_id
    async with store._lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE real_jobs SET official_closed_at=? WHERE id="
            "(SELECT real_job_id FROM jobs WHERE id=?)",
            (FIXED_NOW.isoformat(), ids["parent-closed"]),
        )
        await connection.execute(
            "UPDATE real_jobs SET canonical_posted_at=? WHERE id="
            "(SELECT real_job_id FROM jobs WHERE id=?)",
            ((cutoff - timedelta(days=1)).isoformat(), ids["canonical-old"]),
        )
        await connection.commit()
    assert set(await store.list_application_backfill_ids(since=cutoff)) == {
        ids[key] for key in ("recent-posting", "unknown-recent", "boundary")
    }
    assert ids["parent-closed"] not in await store.list_application_backfill_ids()


async def test_positive_backfill_preserves_sources_and_evaluation_and_reuses_cache(
    store,
):
    first = await _save(store, "1", apply_url="https://careers.example.test/one")
    second = await _save(store, "2")
    before = [await store.get_job(first), await store.get_job(second)]
    await store.save_hard_filters({first: "historical filter"})
    evaluation = await store.get_evaluation(first)
    session = await _lease(store)
    calls, snapshots = [], []

    async def resolve(job):
        calls.append(job.id)
        return ApplicationRouteOutcome("resolved", ats_url=_ATS)

    service = ApplicationBackfillService(store, resolve)
    run = await service.run(
        [first, second, first],
        lease_session=session,
        on_progress=lambda value: snapshots.append(dict(value.scan_progress)),
    )
    assert run is session.run
    assert run.jobs_discovered == len(before)
    assert run.errors == 0
    assert run.stage_a_scored == run.stage_b_scored == run.jobs_scored == 0
    assert run.total_llm_cost_usd == 0
    assert [await store.get_job(first), await store.get_job(second)] == before
    assert await store.get_evaluation(first) == evaluation
    assert len(await store.resolve_real_job_ids([first, second])) == 1
    assert set(calls) == {first, second}
    assert snapshots
    progress = run.scan_progress["application_resolution"]
    assert progress["phase"] == "completed"
    assert progress["processed"] == progress["total"] == len(before)
    assert progress["resolved"] == len(before)
    await service.run([first, second], lease_session=session, on_progress=None)
    assert set(calls) == {first, second}
    assert len(calls) == len(before)


async def test_negative_routes_do_not_merge_and_count_only_blocked_failed_errors(store):
    outcomes = ("unresolved", "ambiguous", "blocked", "failed")
    ids = [await _save(store, str(index)) for index in range(len(outcomes))]
    status_by_id = dict(zip(ids, outcomes, strict=True))
    session = await _lease(store)

    async def resolve(job):
        return ApplicationRouteOutcome(status_by_id[job.id], reason="test negative")

    run = await ApplicationBackfillService(store, resolve).run(
        [*ids, "999999"], lease_session=session, on_progress=None
    )
    assert len(await store.resolve_real_job_ids(ids)) == len(ids)
    assert run.errors == len(outcomes[2:])
    progress = run.scan_progress["application_resolution"]
    assert progress["skipped"] == 1
    assert progress["processed"] == progress["total"] == len(ids) + 1
    for status in outcomes:
        assert progress[status] == 1
    for job_id in ids:
        job = await store.get_job(job_id)
        state = await store.get_state(
            f"application-resolution:{job.platform}:{job.canonical_id}"
        )
        assert json.loads(state)["status"] == status_by_id[job_id]


@pytest.mark.parametrize("interruption", ["cancel", "lease"])
async def test_stop_or_lease_loss_cancels_owned_routes_without_commits(
    store, interruption
):
    job_id = await _save(store, "stopped")
    session = await _lease(store)
    started, finish, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def resolve(_job):
        started.set()
        try:
            await finish.wait()
            return ApplicationRouteOutcome("resolved", ats_url=_ATS)
        finally:
            stopped.set()

    task = asyncio.create_task(
        ApplicationBackfillService(store, resolve).run(
            [job_id], lease_session=session, on_progress=None
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    if interruption == "cancel":
        task.cancel()
        expected = asyncio.CancelledError
    else:
        session._lost.set()
        finish.set()
        expected = RunLeaseLostError
    with pytest.raises(expected):
        await task
    assert stopped.is_set()
    assert await store.get_state("application-resolution:linkedin:stopped") is None
    assert (await store.get_job(job_id)).apply_url is None


async def test_fenced_transaction_rejects_stale_generation(store):
    job_id = await _save(store, "stale")
    session = await _lease(store)
    session.generation += 1

    async def resolve(_job):
        return ApplicationRouteOutcome("resolved", ats_url=_ATS)

    with pytest.raises(RuntimeError, match="Scan write lease lost"):
        await ApplicationBackfillService(store, resolve).run(
            [job_id], lease_session=session, on_progress=None
        )
    assert await store.get_state("application-resolution:linkedin:stale") is None


async def test_resume_reuses_committed_outcome_after_partial_cancellation(store):
    first = await _save(store, "completed")
    second = await _save(store, "pending")
    session = await _lease(store)
    committed, pending = asyncio.Event(), asyncio.Event()
    calls = []

    async def resolve(job):
        calls.append(job.id)
        if job.id == second:
            pending.set()
            await asyncio.Event().wait()
        return ApplicationRouteOutcome("unresolved", reason="no external link")

    def progress(run):
        if run.scan_processed == 1:
            committed.set()

    task = asyncio.create_task(
        ApplicationBackfillService(store, resolve).run(
            [first, second], lease_session=session, on_progress=progress
        )
    )
    await asyncio.wait_for(committed.wait(), 1)
    await asyncio.wait_for(pending.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.run.scan_processed == 1
    assert await store.get_state("application-resolution:linkedin:completed")
    assert await store.get_state("application-resolution:linkedin:pending") is None

    async def resume(job):
        calls.append(job.id)
        return ApplicationRouteOutcome("unresolved", reason="no external link")

    await ApplicationBackfillService(store, resume).run(
        [first, second], lease_session=session
    )
    assert calls.count(first) == 1
    assert calls.count(second) == len(["initial", "resume"])
    assert session.run.scan_progress["application_resolution"]["phase"] == "completed"
