"""Three dedicated readers must overlap source scans and cancel independently."""

import asyncio

import pytest

from jobfeed.services.jobright_bridge import JobrightBridge

BROWSER_WORKERS = 3


async def test_three_application_calls_overlap_scan_and_fourth_waits():
    bridge = JobrightBridge()
    connection = bridge.connect(["linkedin", "application-resolution"])
    options = {
        "max_jobs": 1,
        "batch_size": 1,
        "pacing_s": 0,
        "timeout_s": 5,
        "on_progress": lambda _: None,
    }
    scan = asyncio.create_task(bridge.run_scan(source="linkedin", **options))
    readers = [
        asyncio.create_task(
            bridge.run_scan(
                source="application-resolution",
                targets=[{"id": str(i), "url": f"https://example.test/{i}"}],
                **options,
            )
        )
        for i in range(4)
    ]
    try:
        commands = [
            await asyncio.wait_for(connection.next_command(), 1) for _ in range(4)
        ]
        assert (
            sum(c["source"] == "application-resolution" for c in commands)
            == BROWSER_WORKERS
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(connection.next_command(), 0.01)
        readers[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await readers[0]
        assert (await connection.next_command())["type"] == "cancel"
        fourth = await asyncio.wait_for(connection.next_command(), 1)
        assert fourth["source"] == "application-resolution"
        for command in [*commands, fourth]:
            await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await scan == []
        assert await asyncio.gather(*readers[1:]) == [[], [], []]
    finally:
        for task in [scan, *readers]:
            task.cancel()
        await asyncio.gather(scan, *readers, return_exceptions=True)
