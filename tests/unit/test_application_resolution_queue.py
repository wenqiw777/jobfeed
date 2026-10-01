"""Overlapping route work must preserve every source and stop before evaluation."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.store._application_identity_verification import (
    verification_facts_match,
)
from jobfeed.domain.application_route import ApplicationRouteOutcome
from jobfeed.domain.models import JobPosting
from jobfeed.services.application_resolution import ApplicationResolutionQueue

BROWSER_WORKERS = 3


def posting(key, url="https://careers.example.test/job/one"):
    return JobPosting(
        id=key,
        platform="linkedin",
        canonical_id=key,
        url=f"https://www.linkedin.com/jobs/view/{key}/",
        apply_url=url,
        company="Acme",
        title="Software Engineer",
        location="Boston",
        discovered_at=datetime.now(UTC),
        jd_text="Original description",
    )


class Store:
    def __init__(self):
        self.jobs = {key: posting(key) for key in ("1", "2", "3", "4")}
        self.states = {}
        self.writes = []

    async def get_job(self, key):
        return self.jobs.get(key)

    async def get_state(self, key):
        return self.states.get(key)

    async def record_application_identity(self, **values):
        current = self.jobs[values["job_id"]]
        if current.apply_url != values["expected_apply_url"]:
            return False
        if values["ats_url"] and not verification_facts_match(
            current, values["state_value"]
        ):
            return False
        self.writes.append((values, current.jd_text))
        self.states[values["state_key"]] = values["state_value"]
        return True


async def test_same_pending_url_is_fetched_once_and_links_both_batches():
    store = Store()
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def resolve(job):
        calls.append(job.id)
        started.set()
        await finish.wait()
        return ApplicationRouteOutcome(
            status="resolved",
            ats_url="https://jobs.lever.co/acme/00000000-0000-0000-0000-000000000001",
        )

    async with ApplicationResolutionQueue(store, resolve) as queue:
        await queue.submit_id("1")
        await asyncio.wait_for(started.wait(), 1)
        await queue.submit_id("2")
        drain = asyncio.create_task(queue.drain())
        await asyncio.sleep(0)
        assert not drain.done()
        finish.set()
        await drain
    assert len(calls) == 1
    assert {item[0]["job_id"] for item in store.writes} == {"1", "2"}
    assert (
        next(text for item, text in store.writes if item["job_id"] == "1")
        == "Original description"
    )


async def test_three_routes_can_overlap_and_limit_is_shared_across_batches():
    store = Store()
    for key in store.jobs:
        store.jobs[key] = replace(
            store.jobs[key], apply_url=f"https://careers.example.test/job/{key}"
        )
    started, release = asyncio.Event(), asyncio.Event()
    active, maximum = 0, 0

    async def resolve(_job):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == BROWSER_WORKERS:
            started.set()
        await release.wait()
        active -= 1
        return ApplicationRouteOutcome(
            status="unresolved", reason="no_target_apply_link"
        )

    async with ApplicationResolutionQueue(store, resolve) as queue:
        for key in store.jobs:
            await queue.submit_id(key)
        await asyncio.wait_for(started.wait(), 1)
        assert maximum == BROWSER_WORKERS
        release.set()
    assert len(store.writes) == len(store.jobs)
    assert all(item[0]["ats_url"] is None for item in store.writes)


async def test_stop_cancels_workers_and_never_writes_late_evidence():
    store = Store()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def resolve(_job):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def scan():
        async with ApplicationResolutionQueue(store, resolve) as queue:
            await queue.submit_id("1")
            await started.wait()
            await queue.drain()

    task = asyncio.create_task(scan())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert store.writes == []


async def test_changed_apply_url_rejects_stale_route_and_failure_is_propagated():
    store = Store()

    async def resolve(job):
        store.jobs[job.id] = replace(
            job, apply_url="https://careers.example.test/job/changed"
        )
        return ApplicationRouteOutcome(
            status="resolved", ats_url="https://ats.example.test/one"
        )

    async with ApplicationResolutionQueue(store, resolve) as queue:
        await queue.submit_id("1")
    assert not store.writes

    async def broken(_job):
        raise RuntimeError("identity storage unavailable")

    async with ApplicationResolutionQueue(store, broken) as queue:
        await queue.submit_id("1")
    assert json.loads(store.writes[0][0]["state_value"])["status"] == "failed"


async def test_different_source_jd_never_shares_or_reuses_another_source_proof():
    store = Store()
    store.jobs["2"] = replace(
        store.jobs["2"], jd_text="Different actual job requirements"
    )
    calls = []

    async def resolve(job):
        calls.append(job.id)
        return ApplicationRouteOutcome(
            status="resolved" if job.id == "1" else "unresolved",
            ats_url="https://ats.example.test/one" if job.id == "1" else None,
        )

    async with ApplicationResolutionQueue(store, resolve) as queue:
        await queue.submit_id("1")
        await queue.submit_id("2")
    assert set(calls) == {"1", "2"}
    assert (
        next(item[0]["ats_url"] for item in store.writes if item[0]["job_id"] == "2")
        is None
    )

    calls.clear()
    async with ApplicationResolutionQueue(store, resolve) as queue:
        await queue.submit_id("1")
    assert calls == []
    store.jobs["1"] = replace(store.jobs["1"], jd_text="Corrected qualifications")
    async with ApplicationResolutionQueue(store, resolve) as queue:
        await queue.submit_id("1")
    assert calls == ["1"]


async def test_jd_changed_during_request_is_preserved_and_verified_again():
    store = Store()
    calls = []

    async def resolve(job):
        calls.append(job.jd_text)
        if len(calls) == 1:
            store.jobs[job.id] = replace(job, jd_text="Updated while resolving")
        return ApplicationRouteOutcome(
            status="resolved", ats_url="https://ats.example.test/one"
        )

    async with ApplicationResolutionQueue(store, resolve) as queue:
        await queue.submit_id("1")
    assert calls == ["Original description", "Updated while resolving"]
    assert len(store.writes) == 1
    assert store.writes[0][1] == "Updated while resolving"


async def test_database_failure_unblocks_saturated_queue_and_stops_run():
    ready, release = asyncio.Event(), asyncio.Event()

    class BrokenStore(Store):
        async def record_application_identity(self, **_values):
            raise RuntimeError("identity write failed")

    store = BrokenStore()
    for key in map(str, range(8)):
        store.jobs[key] = posting(key, f"https://careers.example.test/{key}")

    async def resolve(_job):
        ready.set()
        await release.wait()
        return ApplicationRouteOutcome(status="unresolved")

    async def run():
        async with ApplicationResolutionQueue(store, resolve, queue_size=1) as queue:
            for key in store.jobs:
                await queue.submit_id(key)

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(ready.wait(), 1)
        release.set()
        with pytest.raises(RuntimeError, match="identity write failed"):
            await asyncio.wait_for(task, 1)
        assert store.writes == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
