"""Chrome application snapshots are read-only and strictly task-correlated."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from jobfeed.adapters.sources.application_route_browser import (
    ChromeApplicationPageReader,
)
from jobfeed.domain.application_route import ApplicationPageReadError
from jobfeed.domain.models import JobPosting
from jobfeed.services.jobright_bridge import JobrightBridgeError

BROWSER_TIME_BUDGET = 30


def posting():
    return JobPosting(
        platform="linkedin",
        canonical_id="123",
        title="Engineer",
        company="Acme",
        location="Boston",
        discovered_at=datetime.now(UTC),
        url="https://www.linkedin.com/jobs/view/123/",
        apply_url="https://careers.example/jobs/123",
        jd_text="Original source description",
    )


async def test_reader_uses_dedicated_lane_and_preserves_every_source_fact():
    bridge = AsyncMock()
    job = posting()
    original = JobPosting(**job.__dict__)
    url = "https://careers.example/jobs/123?from=LinkedIn"

    async def scan(**kwargs):
        assert kwargs["source"] == "application-resolution"
        assert kwargs["max_jobs"] == kwargs["batch_size"] == 1
        assert kwargs["pacing_s"] == 0
        assert kwargs["timeout_s"] <= BROWSER_TIME_BUDGET
        assert kwargs.get("on_discovery") is None
        target = kwargs["targets"][0]
        assert json.loads(target["id"]) == [job.platform, job.canonical_id, url]
        assert target["url"] == url
        assert target["company"] == job.company
        return [
            {
                "id": target["id"],
                "source": "application-resolution",
                "url": "https://careers.example/jobs/123",
                "html": "<h1>Engineer</h1>",
            }
        ]

    bridge.run_scan.side_effect = scan
    page = await ChromeApplicationPageReader(bridge)(job, url)
    assert page.url == "https://careers.example/jobs/123"
    assert page.html == "<h1>Engineer</h1>"
    assert job == original


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "another-task"},
        {"source": "linkedin"},
        {"html": None},
        {"html": ""},
        {"url": "file:///tmp/job.html"},
        {"url": "https://user:password@careers.example/jobs/1"},
    ],
)
async def test_reader_rejects_mismatched_or_unreadable_snapshots(changes):
    bridge = AsyncMock()

    async def scan(**kwargs):
        return [
            {
                "id": kwargs["targets"][0]["id"],
                "source": "application-resolution",
                "url": "https://careers.example/jobs/123",
                "html": "<h1>Engineer</h1>",
                **changes,
            }
        ]

    bridge.run_scan.side_effect = scan
    with pytest.raises(ApplicationPageReadError) as caught:
        await ChromeApplicationPageReader(bridge)(posting(), posting().apply_url)
    assert caught.value.status == "failed"


@pytest.mark.parametrize(
    "code,status",
    [
        ("missing_permission", "blocked"),
        ("page_timeout", "blocked"),
        ("parse_failed", "failed"),
    ],
)
async def test_reader_preserves_explicit_browser_failure_status(code, status):
    bridge = AsyncMock()

    async def scan(**kwargs):
        return [
            {
                "id": kwargs["targets"][0]["id"],
                "source": "application-resolution",
                "error": "page unavailable",
                "error_code": code,
            }
        ]

    bridge.run_scan.side_effect = scan
    with pytest.raises(ApplicationPageReadError) as caught:
        await ChromeApplicationPageReader(bridge)(posting(), posting().apply_url)
    assert caught.value.status == status
    assert code in caught.value.reason


async def test_missing_extension_is_blocked_and_empty_result_is_a_failure():
    bridge = AsyncMock()
    bridge.run_scan.side_effect = JobrightBridgeError("extension not connected")
    reader = ChromeApplicationPageReader(bridge)
    with pytest.raises(ApplicationPageReadError) as caught:
        await reader(posting(), posting().apply_url)
    assert caught.value.status == "blocked"
    bridge.run_scan.side_effect = None
    bridge.run_scan.return_value = []
    with pytest.raises(ApplicationPageReadError) as caught:
        await reader(posting(), posting().apply_url)
    assert caught.value.status == "failed"


async def test_reader_cancellation_propagates_to_the_owned_bridge_call():
    bridge = AsyncMock()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def scan(**_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    bridge.run_scan.side_effect = scan
    task = asyncio.create_task(
        ChromeApplicationPageReader(bridge)(posting(), posting().apply_url)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
