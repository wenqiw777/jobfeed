import asyncio
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.sources.jobboard_extension import (
    JobboardExtensionSource,
    map_board_job,
)
from jobfeed.config_sources import SourcesBoardExtensionConfig
from jobfeed.ports.source import StoredEnrichment
from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError


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
