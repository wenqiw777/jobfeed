"""Four source lanes must enter concurrently and retain task ownership."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import TimeoutError as RedisTimeout

from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError


async def test_completion_warning_survives_bridge():
    bridge = JobrightBridge()
    connection = bridge.connect(["linkedin"])
    task = asyncio.create_task(
        bridge.run_scan(
            source="linkedin",
            max_jobs=1,
            batch_size=1,
            pacing_s=0,
            timeout_s=5,
            on_progress=lambda _: None,
        )
    )
    command = await connection.next_command()
    await bridge.receive(
        {
            "type": "complete",
            "task_id": command["task_id"],
            "warning": "Unconfirmed empty page",
        }
    )
    with pytest.raises(JobrightBridgeError) as caught:
        await task
    assert caught.value.warning is True


async def test_four_lanes_enter_before_any_completes_and_cancel_is_isolated():
    bridge = JobrightBridge()
    connection = bridge.connect(["jobright", "linkedin", "handshake", "github-jd"])
    tasks = [
        asyncio.create_task(
            bridge.run_scan(
                source=source,
                max_jobs=1,
                batch_size=1,
                pacing_s=0,
                timeout_s=5,
                on_progress=lambda _: None,
            )
        )
        for source in ["jobright", "linkedin", "handshake", "github-jd"]
    ]
    try:
        commands = [
            await asyncio.wait_for(connection.next_command(), 0.2) for _ in range(4)
        ]
        with pytest.raises(JobrightBridgeError, match="busy"):
            await bridge.run_scan(
                source="jobright",
                max_jobs=1,
                batch_size=1,
                pacing_s=0,
                timeout_s=1,
                on_progress=lambda _: None,
            )
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert (await connection.next_command())["task_id"] == commands[0]["task_id"]
        for command in reversed(commands[1:]):
            await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await asyncio.gather(*tasks[1:]) == [[], [], []]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_one_lane_persistence_failure_does_not_disconnect_other_sources():
    bridge = JobrightBridge()
    connection = bridge.connect(["handshake", "linkedin"])
    options = {
        "max_jobs": 1,
        "batch_size": 1,
        "pacing_s": 0,
        "timeout_s": 5,
        "on_progress": lambda _: None,
    }
    broken = asyncio.create_task(
        bridge.run_scan(
            source="handshake",
            **options,
            on_batch=AsyncMock(
                side_effect=RedisTimeout("partial persistence timed out")
            ),
        )
    )
    healthy = asyncio.create_task(bridge.run_scan(source="linkedin", **options))
    first = await connection.next_command()
    second = await connection.next_command()
    try:
        await bridge.receive(
            {
                "type": "batch",
                "task_id": first["task_id"],
                "jobs": [{"id": "one", "source": "handshake"}],
            }
        )
        with pytest.raises(RedisTimeout):
            await broken
        assert bridge.connected
        await bridge.receive({"type": "complete", "task_id": first["task_id"]})
        cancel = await asyncio.wait_for(connection.next_command(), 1)
        assert cancel == {"type": "cancel", "task_id": first["task_id"]}
        await bridge.receive({"type": "complete", "task_id": second["task_id"]})
        assert await healthy == []
    finally:
        broken.cancel()
        healthy.cancel()
        await asyncio.gather(broken, healthy, return_exceptions=True)
