import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from jobfeed.adapters.sources.jobboard_extension import (
    JobboardExtensionSource,
    map_board_job,
)
from jobfeed.config_sources import SourcesBoardExtensionConfig
from jobfeed.domain.models import LLMResponse
from jobfeed.ports.source import SourceFetchProgress, StoredEnrichment
from jobfeed.services.job_page_extraction import JobPageExtractor
from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError


async def test_observation_progress_does_not_add_jobs_or_disconnect_scan():
    bridge = JobrightBridge()
    connection = bridge.connect(sources=["linkedin"])
    progress = []
    task = asyncio.create_task(
        bridge.run_scan(
            source="linkedin",
            query="SWE",
            max_jobs=10,
            batch_size=25,
            pacing_s=0,
            timeout_s=2,
            on_progress=progress.append,
        )
    )
    command = await connection.next_command()
    try:
        assert command["progress_events"] is True
        await bridge.receive(
            {
                "type": "progress",
                "task_id": command["task_id"],
                "phase": "details",
                "processed": 2,
                "total": 5,
                "current_job_id": "2",
            }
        )
        assert progress[-1].phase == "details"
        # Bad observation data must not terminate the shared websocket/task.
        await bridge.receive(
            {
                "type": "progress",
                "task_id": command["task_id"],
                "phase": "details",
                "processed": -1,
            }
        )
        await bridge.receive(
            {
                "type": "progress",
                "task_id": command["task_id"],
                "phase": [],
                "processed": 0,
            }
        )
        assert len(progress) == 1
        await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await task == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unknown_progress_cannot_disconnect_two_active_lanes():
    bridge = JobrightBridge()
    connection = bridge.connect(sources=["linkedin", "handshake"])
    tasks = [
        asyncio.create_task(
            bridge.run_scan(
                source=source,
                query="SWE",
                max_jobs=10,
                batch_size=25,
                pacing_s=0,
                timeout_s=2,
                on_progress=lambda _: None,
            )
        )
        for source in ["linkedin", "handshake"]
    ]
    commands = [await connection.next_command(), await connection.next_command()]
    try:
        for message in [{"task_id": "long-retired"}, {}, {"task_id": []}]:
            await bridge.receive(
                {"type": "progress", "phase": "details", "processed": 1, **message}
            )
        for command in commands:
            await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await asyncio.gather(*tasks) == [[], []]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_interpreting_progress_is_visible_while_model_waits():
    release = asyncio.Event()
    started = asyncio.Event()
    client = AsyncMock()

    async def complete(_request):
        started.set()
        await release.wait()
        return LLMResponse(
            content=json.dumps(
                {
                    "identity_status": "ambiguous",
                    "status": "unavailable",
                    "identity_block_ids": [],
                    "description_block_ids": [],
                    "repost_block_ids": [],
                }
            ),
            model="test",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0,
        )

    client.complete.side_effect = complete
    bridge = AsyncMock()
    bridge.run_scan.return_value = [
        {
            "id": "1",
            "source": "linkedin",
            "title": "SWE",
            "url": "https://www.linkedin.com/jobs/view/1/",
            "description": "Existing JD",
            "page_snapshot": {
                "blocks": [
                    {"id": 0, "text": "Reposted today", "path": [1], "links": []}
                ]
            },
        }
    ]
    source = JobboardExtensionSource(
        source="linkedin",
        bridge=bridge,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["SWE"]),
        page_extractor=JobPageExtractor(client, model="test"),
    )
    progress = []
    task = asyncio.create_task(source.fetch_jobs_with_progress({}, progress.append))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert progress[-1].phase == "interpreting"
        assert (progress[-1].processed, progress[-1].total) == (0, 1)
    finally:
        release.set()
        await task
    assert any(p.phase == "interpreting" and p.processed == 1 for p in progress)


async def test_source_preserves_detail_and_rate_limit_phases():

    bridge = AsyncMock()

    async def scan(**kwargs):
        kwargs["on_progress"](
            SourceFetchProgress(phase="details", processed=2, total=25)
        )
        kwargs["on_progress"](
            SourceFetchProgress(phase="rate_limited", processed=2, total=25)
        )
        return []

    bridge.run_scan.side_effect = scan
    source = JobboardExtensionSource(
        source="linkedin",
        bridge=bridge,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["SWE"]),
    )
    progress = []
    await source.fetch_jobs_with_progress({}, progress.append)
    assert [p.phase for p in progress] == ["details", "rate_limited", "fetched"]
    assert progress[0].total == 25


async def test_old_extension_rejects_board_scan_before_dispatch():
    bridge = JobrightBridge()
    bridge.connect()
    with pytest.raises(JobrightBridgeError, match="reload"):
        await bridge.run_scan(
            source="handshake",
            query="AI Engineer New Grad",
            max_jobs=2,
            batch_size=25,
            pacing_s=1,
            timeout_s=2,
            on_progress=lambda _: None,
        )


async def test_board_bridge_routes_and_deduplicates_only_expected_source():
    bridge = JobrightBridge()
    connection = bridge.connect(sources=["jobright", "linkedin", "handshake"])
    task = asyncio.create_task(
        bridge.run_scan(
            source="handshake",
            query="AI Engineer New Grad",
            max_jobs=2,
            batch_size=25,
            pacing_s=1,
            timeout_s=2,
            on_progress=lambda _: None,
        )
    )
    command = await connection.next_command()
    assert command["type"] == "start_board_scan"
    assert command["source"] == "handshake"
    assert command["query"] == "AI Engineer New Grad"
    await bridge.receive(
        {
            "type": "batch",
            "task_id": command["task_id"],
            "jobs": [
                {"source": "linkedin", "id": "wrong"},
                {"source": "handshake", "id": "1"},
                {"source": "handshake", "id": "1"},
                {"source": "handshake", "id": "2"},
            ],
        }
    )
    await bridge.receive({"type": "complete", "task_id": command["task_id"]})
    assert [j["id"] for j in await task] == ["1", "2"]


def test_handshake_mapping_retains_entire_description():

    text = "Build software. " * 400
    job = map_board_job(
        {
            "source": "handshake",
            "id": "123",
            "title": "Engineer",
            "url": "https://app.joinhandshake.com/jobs/123",
            "employer": {"name": "Acme"},
            "locations": [{"displayName": "Detroit"}],
            "descriptionFormat": "html",
            "description": f"<p>{text}</p><p>Qualifications: Python &amp; SQL.</p>",
        },
        discovered_at=datetime.now(UTC),
    )
    assert text.strip() in job.jd_text
    assert "Qualifications: Python & SQL." in job.jd_text
    assert job.canonical_id == "123"
    assert job.enrich_source == "handshake_extension"
    assert job.location == "Detroit"


async def test_handshake_bootstrap_relevance_then_newest_and_later_newest_only():
    calls = []

    class Bridge:
        async def run_scan(self, **kwargs):
            calls.append(kwargs["sort"])
            return [
                {
                    "source": "handshake",
                    "id": "1",
                    "title": "Engineer",
                    "url": "https://app.joinhandshake.com/jobs/1",
                    "description": "Full JD",
                }
            ]

    class Store:
        def __init__(self):
            self.state = {}

        async def get_state(self, key):
            return self.state.get(key)

        async def set_state(self, key, value):
            self.state[key] = value

        async def get_enrichment(self, **_kwargs):
            return StoredEnrichment(jd_text="Full JD", quality=None, enriched_at=None)

    source = JobboardExtensionSource(
        source="handshake",
        config=SourcesBoardExtensionConfig(queries=["SWE"]),
        bridge=Bridge(),
        store=Store(),
    )
    assert len(await source.fetch_jobs({})) == 1
    assert calls == ["relevance", "newest"]
    calls.clear()
    await source.fetch_jobs({})
    assert calls == ["newest"]


async def test_category_search_uses_native_filters_without_keyword_or_bootstrap():
    calls = []

    class Bridge:
        async def run_scan(self, **kwargs):
            calls.append(kwargs)
            return []

    config = SourcesBoardExtensionConfig(
        search_url="https://app.joinhandshake.com/job-search/11398096?jobRoleGroups=64&per_page=25&employmentTypes=1&jobType=9&sort=posted_date_desc&page=1"
    )
    source = JobboardExtensionSource(source="handshake", config=config, bridge=Bridge())
    await source.fetch_jobs({})
    assert len(calls) == 1
    assert calls[0]["query"] == ""
    assert calls[0]["sort"] == "newest"
    assert calls[0]["max_jobs"] == config.max_jobs
    assert calls[0]["filters"] == {
        "jobRoleGroupIds": ["64"],
        "employmentTypeIds": ["1"],
        "jobTypeIds": ["9"],
    }


async def test_linkedin_each_search_gets_500_and_results_deduplicate_by_id():
    calls = []

    class Bridge:
        async def run_scan(self, **kwargs):
            calls.append(kwargs)
            return [
                {
                    "source": "linkedin",
                    "id": str(i),
                    "title": "Engineer",
                    "url": f"https://www.linkedin.com/jobs/view/{i}/",
                    "description": "Complete description",
                }
                for i in range(
                    0 if len(calls) == 1 else 400, 500 if len(calls) == 1 else 900
                )
            ][: kwargs["max_jobs"]]

    source = JobboardExtensionSource(
        source="linkedin",
        config=SourcesBoardExtensionConfig(queries=["Software", "AI"], max_jobs=500),
        bridge=Bridge(),
    )
    jobs = await source.fetch_jobs({})
    assert [call["max_jobs"] for call in calls] == [500, 500]
    expected_unique_jobs = 900
    assert len(jobs) == expected_unique_jobs
    assert all(job.jd_text == "Complete description" for job in jobs)
