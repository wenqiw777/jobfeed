"""Redis delivery, replay and ownership tests against a real local server."""

import asyncio
import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError, ResponseError
from redis.exceptions import TimeoutError as RedisTimeout

from jobfeed.adapters.queue.redis_pipeline import _COMPLETE, _ENQUEUE, RedisPipeline
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting
from jobfeed.ports.source import PartialSourceFetchError
from jobfeed.scan_wiring import build_scan_service
from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.services.pipeline_context import current_pipeline, durable_posting
from jobfeed.services.run_manager import RunManager

ONE_DAY_SECONDS = 24 * 60 * 60
MISSING_TTL = -2


@pytest.fixture
async def redis_client():
    client = Redis.from_url(
        os.environ.get("JOBFEED_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        decode_responses=True,
    )
    try:
        await client.ping()
    except ConnectionError:
        await client.aclose()
        if os.environ.get("JOBFEED_TEST_REDIS_URL"):
            raise
        pytest.skip("local Redis not running; set JOBFEED_TEST_REDIS_URL in CI")
    yield client
    await client.aclose()


async def test_accepted_input_and_completed_result_survive_worker_replacement(
    redis_client,
):
    namespace = f"test:{uuid4()}"
    first = RedisPipeline(redis_client, namespace=namespace)
    await first.start("run-a", generation=1)
    calls = []

    async def crash(payload):
        calls.append(payload)
        raise RuntimeError("worker died")

    with pytest.raises(RuntimeError, match="worker died"):
        await first.step("native:job-1", {"url": "official"}, crash)
    second = RedisPipeline(redis_client, namespace=namespace)
    await second.start("run-b", generation=2, resume_from="run-a")

    async def succeed(payload):
        calls.append(payload)
        return {"jd": "complete"}

    assert await second.step("native:job-1", {"url": "changed"}, succeed) == {
        "jd": "complete"
    }
    assert calls == [{"url": "official"}, {"url": "official"}]
    assert await second.step("native:job-1", {}, crash) == {"jd": "complete"}
    assert await second.pending_count() == 0
    with pytest.raises(ResponseError, match="ownership"):
        await first.step("native:other", {}, succeed)


async def test_partial_browser_results_are_available_after_restart(redis_client):
    namespace = f"test:{uuid4()}"
    first = RedisPipeline(redis_client, namespace=namespace)
    await first.start("a", generation=1)
    await first.save_partial("browser", [{"id": "one", "description": "full"}])
    second = RedisPipeline(redis_client, namespace=namespace)
    await second.start("b", generation=2, resume_from="a")
    assert await second.load_partial("browser") == [
        {"id": "one", "description": "full"}
    ]


async def test_completion_refuses_pending_work(redis_client):
    pipeline = RedisPipeline(redis_client, namespace=f"test:{uuid4()}")
    await pipeline.start("pending", generation=1)

    async def fail(_):
        raise RuntimeError("incomplete")

    with pytest.raises(RuntimeError, match="incomplete"):
        await pipeline.step("job", {}, fail)
    with pytest.raises(RuntimeError, match="pending"):
        await pipeline.assert_drained()

    async def succeed(_):
        return {"done": True}

    await pipeline.step("job", {}, succeed)
    await pipeline.assert_drained()


async def test_four_lanes_and_commit_before_ack_replay(
    redis_client, tmp_path, monkeypatch
):
    store = SQLiteStore(tmp_path / "scan.sqlite")
    await store.connect()
    namespace = f"test:{uuid4()}"
    entered = set()
    lane_count = 4
    all_entered = asyncio.Event()
    calls = []

    class Source:
        def __init__(self, name):
            self.name = name

        async def fetch_jobs(self, _config):
            entered.add(self.name)
            if len(entered) == lane_count:
                all_entered.set()
            await asyncio.wait_for(all_entered.wait(), 2)

            async def enrich(_):
                calls.append(self.name)
                return JobPosting(
                    platform=self.name,
                    canonical_id="one",
                    title="SWE",
                    company="ACME",
                    location="US",
                    url="https://example.com/job",
                    discovered_at=datetime.now(UTC),
                )

            return [await durable_posting(f"native:{self.name}", {}, enrich)]

    original = RedisPipeline.step
    crash = True

    async def crash_before_ack(self, name, payload, work, **kwargs):
        async def wrapped(saved):
            nonlocal crash
            result = await work(saved)
            if name.startswith("write:") and crash:
                crash = False
                raise RuntimeError("injected after commit before ACK")
            return result

        return await original(self, name, payload, wrapped, **kwargs)

    monkeypatch.setattr(RedisPipeline, "step", crash_before_ack)
    manager = RunManager(
        store=store,
        logger=MagicMock(),
        scan_service_factory=lambda: build_scan_service(
            store,
            MagicMock(),
            redis_url=os.environ.get(
                "JOBFEED_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"
            ),
            redis_namespace=namespace,
        ),
        evaluate_service_factory=MagicMock(),
    )
    specs = [(name, Source(name), {}) for name in ["one", "two", "three", "four"]]
    try:
        first = await manager.trigger_scan(specs)
        await manager._tasks[first]
        assert (await store.get_pipeline_run(first)).status == "failed"
        assert 1 <= len(await store.list_jobs()) <= lane_count
        second = await manager.trigger_scan(specs, resume_from_run_id=first)
        await manager._tasks[second]
        run = await store.get_pipeline_run(second)
        assert run.status == "succeeded"
        assert run.jobs_inserted == lane_count
        assert run.jobs_updated == 0
        assert len(await store.list_jobs()) == lane_count
        assert sorted(calls) == ["four", "one", "three", "two"]
        pipeline = RedisPipeline(redis_client, namespace=namespace)
        root = await redis_client.get(f"{namespace}:run:{second}")
        pipeline.prefix = f"{namespace}:pipeline:{root}:"
        assert await pipeline.pending_count() == 0
        assert not [
            key async for key in redis_client.scan_iter(match=pipeline.prefix + "*")
        ]
    finally:
        await store.close()


@pytest.mark.parametrize("gated", [False, True])
async def test_browser_resume_sends_only_unfinished_targets(redis_client, gated):
    namespace = f"test:{uuid4()}"
    first = RedisPipeline(redis_client, namespace=namespace)
    await first.start("first", generation=1)
    bridge = JobrightBridge()
    source = "linkedin" if gated else "github-jd"
    connection = bridge.connect([source, "discovery-gate-v1"])
    targets = [{"id": key, "url": f"https://example.com/{key}"} for key in ["a", "b"]]
    options = {
        "source": source,
        "targets": targets,
        "max_jobs": 2,
        "batch_size": 1,
        "pacing_s": 0,
        "timeout_s": 5,
        "on_progress": lambda _: None,
    }
    if gated:

        async def discover(_rows):
            return {"skip_ids": [], "reused_jobs": []}

        options["on_discovery"] = discover
    token = current_pipeline.set(first)
    task = asyncio.create_task(bridge.run_scan(**options))
    current_pipeline.reset(token)
    command = await asyncio.wait_for(connection.next_command(), 1)
    row = {**targets[0], "source": source, "description": "complete JD"}
    await bridge.receive(
        {"type": "batch", "task_id": command["task_id"], "jobs": [row]}
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await connection.next_command()  # cancellation of the first browser task

    second = RedisPipeline(redis_client, namespace=namespace)
    await second.start("second", generation=2, resume_from="first")
    token = current_pipeline.set(second)
    task = asyncio.create_task(bridge.run_scan(**options))
    current_pipeline.reset(token)
    try:
        command = await asyncio.wait_for(connection.next_command(), 1)
        if gated:
            assert command["discovery_gate"] is True
            assert command["cached_jobs"] == [row]
        else:
            assert command["targets"] == [targets[1]]
        await bridge.receive(
            {
                "type": "batch",
                "task_id": command["task_id"],
                "jobs": [{**targets[1], "source": source, "description": "second JD"}],
            }
        )
        await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert {row["id"] for row in await task} == {"a", "b"}
        await second.assert_drained()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_terminal_source_error_retry_does_not_replay_cached_error(
    redis_client, tmp_path
):
    store = SQLiteStore(tmp_path / "retry.sqlite")
    await store.connect()
    namespace = f"test:{uuid4()}"
    calls = 0

    class Source:
        async def fetch_jobs(self, _config):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PartialSourceFetchError("temporary source failure", [])
            return []

    manager = RunManager(
        store=store,
        logger=MagicMock(),
        evaluate_service_factory=MagicMock(),
        scan_service_factory=lambda: build_scan_service(
            store,
            MagicMock(),
            redis_url=os.environ.get(
                "JOBFEED_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"
            ),
            redis_namespace=namespace,
        ),
    )
    try:
        specs = [("source", Source(), {})]
        first = await manager.trigger_scan(specs)
        await manager._tasks[first]
        assert (await store.get_pipeline_run(first)).status == "failed"
        second = await manager.trigger_scan(specs, resume_from_run_id=first)
        await manager._tasks[second]
        assert (await store.get_pipeline_run(second)).status == "succeeded"
        assert calls == len([first, second])
        assert await redis_client.get(f"{namespace}:run:{second}") == second
    finally:
        await store.close()


async def test_step_releases_partial_only_after_durable_completion(redis_client):
    pipeline = RedisPipeline(redis_client, namespace=f"test:{uuid4()}")
    await pipeline.start("partial-cleanup", generation=1)
    rows = [{"id": "one", "description": "complete JD"}]
    await pipeline.save_partial("bridge:browser", rows)

    async def fail(_):
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        await pipeline.step("bridge:browser", {}, fail)
    assert await pipeline.load_partial("bridge:browser") == rows

    async def finish(_):
        return {"jobs": rows}

    assert await pipeline.step("bridge:browser", {}, finish) == {"jobs": rows}
    assert await pipeline.load_partial("bridge:browser") == []
    # Old journals can have both a completed result and redundant partials.
    await pipeline.save_partial("bridge:browser", rows)
    assert await pipeline.step("bridge:browser", {}, fail) == {"jobs": rows}
    assert await pipeline.load_partial("bridge:browser") == []


@pytest.mark.parametrize(
    "status,drained,pending",
    [
        ("running", "1", False),
        ("failed", None, False),
        ("succeeded", "1", True),
        ("failed", "1", False),
    ],
)
async def test_release_requires_finalized_drained_journal(
    redis_client, status, drained, pending
):
    namespace = f"test:{uuid4()}"
    pipeline = RedisPipeline(redis_client, namespace=namespace)
    await pipeline.start("release", generation=1)
    await pipeline.save_partial("browser", [{"id": "recover-me"}])
    if pending:
        await redis_client.xadd(pipeline.prefix + "tasks", {"task": "pending"})
    run = MagicMock(run_id="release", status=status)
    store = MagicMock()
    store.get_pipeline_run = AsyncMock(return_value=run)
    store.get_state = AsyncMock(return_value=drained)
    service = build_scan_service(
        store,
        MagicMock(),
        redis_namespace=namespace,
        redis_url=os.environ.get("JOBFEED_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
    )
    await service.release_completed_pipeline(run)
    retained = status == "running" or not drained or pending
    assert bool(await pipeline.load_partial("browser")) == retained
    ttl = await redis_client.ttl(pipeline.prefix + "partial:browser")
    if status == "running":
        assert ttl == -1
    elif retained:
        assert 0 < ttl <= ONE_DAY_SECONDS
        assert 0 < await redis_client.ttl(f"{namespace}:run:release") <= ONE_DAY_SECONDS
    else:
        assert ttl == MISSING_TTL


async def test_resume_removes_terminal_retention_expiry(redis_client):
    namespace = f"test:{uuid4()}"
    first = RedisPipeline(redis_client, namespace=namespace)
    await first.start("first", generation=1)
    await first.save_partial("browser", [{"id": "recover-me"}])
    run = MagicMock(run_id="first", status="failed")
    store = MagicMock()
    store.get_pipeline_run = AsyncMock(return_value=run)
    store.get_state = AsyncMock(return_value=None)
    service = build_scan_service(
        store,
        MagicMock(),
        redis_namespace=namespace,
        redis_url=os.environ.get("JOBFEED_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
    )
    await service.release_completed_pipeline(run)
    assert (
        0 < await redis_client.ttl(first.prefix + "partial:browser") <= ONE_DAY_SECONDS
    )

    replacement = RedisPipeline(redis_client, namespace=namespace)
    await replacement.start("second", generation=2, resume_from="first")
    assert await replacement.load_partial("browser") == [{"id": "recover-me"}]
    assert await redis_client.ttl(first.prefix + "partial:browser") == -1


async def test_failed_result_retries_preserving_partial_and_success_cache(redis_client):
    pipeline = RedisPipeline(redis_client, namespace=f"test:{uuid4()}")
    await pipeline.start("first", generation=1)
    old = {"jobs": [{"id": "old", "description": "saved"}], "error": "aborted"}
    await pipeline.step("bridge:retry", {}, AsyncMock(return_value=old))
    replacement = RedisPipeline(redis_client, namespace=pipeline.namespace)
    await replacement.start("second", generation=2, resume_from="first")

    async def recover(_):
        assert await replacement.load_partial("bridge:retry") == old["jobs"]
        return {"jobs": old["jobs"] + [{"id": "new"}], "error": None}

    result = await replacement.step("bridge:retry", {}, recover, retry_errors=True)
    assert result["error"] is None
    assert result["generation"] == "2"
    assert [row["id"] for row in result["jobs"]] == ["old", "new"]
    assert await replacement.load_partial("bridge:retry") == []
    unused = AsyncMock(side_effect=AssertionError("success must be reused"))
    assert (
        await replacement.step("bridge:retry", {}, unused, retry_errors=True) == result
    )
    assert await replacement.pending_count() == 0
    with pytest.raises(ResponseError, match="ownership"):
        await pipeline.step("bridge:retry", {}, unused, retry_errors=True)


async def test_mixed_pending_and_partial_failure_retry_saves_new_rows(
    redis_client, tmp_path
):
    assert await redis_client.ping()
    store = SQLiteStore(tmp_path / "mixed-retry.sqlite")
    await store.connect()
    namespace = f"test:{uuid4()}"
    calls = {"partial": 0, "pending": 0}

    class Source:
        def __init__(self, name):
            self.name = name

        async def fetch_jobs(self, _config):
            calls[self.name] += 1
            if self.name == "pending":
                if calls[self.name] == 1:
                    raise RuntimeError("Redis timed out")
                return []

            def job(identifier):
                return JobPosting(
                    platform="partial",
                    canonical_id=identifier,
                    title="SWE",
                    company="ACME",
                    location="US",
                    url=f"https://example.com/{identifier}",
                    discovered_at=datetime.now(UTC),
                )

            if calls[self.name] == 1:
                raise PartialSourceFetchError("browser aborted", [job("old")])
            return [job("new")]

    manager = RunManager(
        store=store,
        logger=MagicMock(),
        evaluate_service_factory=MagicMock(),
        scan_service_factory=lambda: build_scan_service(
            store,
            MagicMock(),
            redis_url=os.environ.get(
                "JOBFEED_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"
            ),
            redis_namespace=namespace,
        ),
    )
    try:
        specs = [(name, Source(name), {}) for name in calls]
        first = await manager.trigger_scan(specs)
        await manager._tasks[first]
        assert (await store.get_pipeline_run(first)).status == "failed"
        second = await manager.trigger_scan(specs, resume_from_run_id=first)
        await manager._tasks[second]
        run = await store.get_pipeline_run(second)
        assert run.status == "succeeded", run.failure_message
        assert calls == {"partial": 2, "pending": 2}
        assert {job.canonical_id for job in await store.list_jobs()} == {"old", "new"}
        assert run.jobs_discovered == len({"old", "new"})
    finally:
        await store.close()


@pytest.mark.parametrize("phase", ["enqueue", "claim", "complete"])
async def test_lost_redis_reply_replays_safe_journal_command_without_duplicate_work(
    redis_client, monkeypatch, phase
):

    pipeline = RedisPipeline(redis_client, namespace=f"test:{uuid4()}")
    await pipeline.start("lost-reply", generation=1)
    real = redis_client.execute_command
    failed = False
    script = _ENQUEUE if phase == "enqueue" else _COMPLETE

    async def lose_reply(*args, **kwargs):
        nonlocal failed
        result = await real(*args, **kwargs)
        if (
            args[0] == "XCLAIM" if phase == "claim" else args[:2] == ("EVAL", script)
        ) and not failed:
            failed = True
            raise RedisTimeout("reply lost after commit")
        return result

    monkeypatch.setattr(redis_client, "execute_command", lose_reply)
    # Construct after patching, matching the runtime client supplied to the journal.
    pipeline = RedisPipeline(redis_client, namespace=pipeline.namespace)
    await pipeline.start("lost-reply", generation=1)
    work = AsyncMock(return_value={"saved": "one"})
    assert await pipeline.step("native:one", {}, work) == {"saved": "one"}
    assert work.await_count == 1
    assert await pipeline.pending_count() == 0


async def test_durable_bridge_delivers_saved_batch_before_scan_finishes(redis_client):
    pipeline = RedisPipeline(redis_client, namespace=f"test:{uuid4()}")
    await pipeline.start("streamed-browser", generation=1)
    bridge = JobrightBridge()
    connection = bridge.connect(["linkedin"])
    batches = []

    async def received(rows):
        identity = {
            "source": "linkedin",
            "query": None,
            "sort": None,
            "search_url": None,
            "filters": None,
        }
        name = "bridge:" + json.dumps(identity, sort_keys=True)
        assert await pipeline.load_partial(name) == rows
        batches.append(rows)

    token = current_pipeline.set(pipeline)
    scan = asyncio.create_task(
        bridge.run_scan(
            source="linkedin",
            max_jobs=1,
            batch_size=1,
            pacing_s=0,
            timeout_s=5,
            on_progress=lambda _: None,
            on_batch=received,
        )
    )
    try:
        command = await asyncio.wait_for(connection.next_command(), 1)
        rows = [{"id": "one", "source": "linkedin"}]
        await bridge.receive(
            {"type": "batch", "task_id": command["task_id"], "jobs": rows}
        )
        if not batches:
            await scan
        assert batches == [rows]
        assert not scan.done()
        await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await scan == rows
    finally:
        scan.cancel()
        await asyncio.gather(scan, return_exceptions=True)
        current_pipeline.reset(token)
