import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from jobfeed.adapters.sources.jobboard_extension import (
    JobboardExtensionSource,
)
from jobfeed.config_sources import SourcesBoardExtensionConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError


@pytest.mark.parametrize(
    "company,expected", [("Unknown", "Noom"), ("Verified Co", "Verified Co")]
)
async def test_cached_full_jd_updates_missing_company_from_discovery(company, expected):
    old = JobPosting(
        platform="linkedin",
        canonical_id="1",
        title="SWE",
        company=company,
        location="US",
        url="https://example.com/1",
        discovered_at=datetime.now(UTC),
        jd_text="Saved complete JD",
        jd_quality=QualityBand.FULL,
    )
    store = AsyncMock()
    store.get_jobs_by_canonical_ids.return_value = {"1": old}
    bridge = AsyncMock()

    async def scan(**kwargs):
        decision = await kwargs["on_discovery"](
            [{"id": "1", "employer": {"name": "Noom"}}]
        )
        assert decision["skip_ids"] == ["1"]
        return decision["reused_jobs"]

    bridge.run_scan.side_effect = scan
    source = JobboardExtensionSource(
        source="linkedin",
        store=store,
        bridge=bridge,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["SWE"]),
    )
    jobs = await source.fetch_jobs({})
    assert jobs[0].company == expected
    assert jobs[0].jd_text == old.jd_text
    assert jobs[0].enriched_at == old.enriched_at


@pytest.mark.parametrize("platform", ["linkedin", "handshake"])
async def test_gate_reuses_complete_jobs_and_deduplicates_across_searches(platform):
    old = JobPosting(
        platform=platform,
        canonical_id="old",
        title="SWE",
        company="ACME",
        location="US",
        url="https://example.com/old",
        discovered_at=datetime.now(UTC),
        jd_text="Complete saved JD",
        jd_quality=QualityBand.FULL,
    )
    store = AsyncMock()
    store.get_state.return_value = None
    store.get_jobs_by_canonical_ids.return_value = {"old": old}
    bridge = AsyncMock()
    calls = []

    async def scan(**kwargs):
        decision = await kwargs["on_discovery"](
            [{"id": "old"}, {"id": "new"}, {"id": "new"}]
        )
        calls.append(decision)
        return decision["reused_jobs"]

    bridge.run_scan.side_effect = scan
    source = JobboardExtensionSource(
        source=platform,
        store=store,
        bridge=bridge,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["first", "second"]),
    )
    jobs = await source.fetch_jobs({})
    assert calls[0]["skip_ids"] == ["old"]
    assert set(calls[1]["skip_ids"]) == {"old", "new"}
    assert calls[1]["reused_jobs"] == []
    assert jobs[0].jd_text == old.jd_text
    assert jobs[0].enriched_at == old.enriched_at
    assert len(jobs) == 1
    store.get_jobs_by_canonical_ids.assert_awaited_once_with(
        platform=platform, canonical_ids=["old", "new"]
    )


async def test_bridge_discovery_gate_matches_task_and_fails_old_extension():
    gate = AsyncMock(return_value={"skip_ids": ["1"], "reused_jobs": []})
    bridge = JobrightBridge()
    connection = bridge.connect(["linkedin"])
    args = {
        "source": "linkedin",
        "query": "SWE",
        "max_jobs": 1,
        "batch_size": 1,
        "pacing_s": 0,
        "timeout_s": 2,
        "on_progress": lambda _: None,
        "on_discovery": gate,
    }
    with pytest.raises(JobrightBridgeError, match="reload"):
        await bridge.run_scan(**args)
    bridge.disconnect(connection)
    connection = bridge.connect(["linkedin", "discovery-gate-v1"])
    task = asyncio.create_task(bridge.run_scan(**args))
    command = await asyncio.wait_for(connection.next_command(), 1)
    try:
        assert command["discovery_gate"] is True
        await bridge.receive(
            {
                "type": "discovery",
                "task_id": command["task_id"],
                "request_id": "page-1",
                "rows": [{"id": "1"}],
            }
        )
        decision = await asyncio.wait_for(connection.next_command(), 1)
        assert decision["type"] == "discovery_result"
        assert decision["request_id"] == "page-1"
        assert decision["skip_ids"] == ["1"]
        await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await task == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "quality,body",
    [
        (QualityBand.PARTIAL, "partial JD"),
        (QualityBand.FULL, "   "),
        (QualityBand.MISSING, None),
    ],
)
async def test_incomplete_or_blank_saved_description_is_not_skipped(quality, body):
    old = JobPosting(
        platform="linkedin",
        canonical_id="1",
        title="SWE",
        company="ACME",
        location="US",
        url="https://example.com/1",
        discovered_at=datetime.now(UTC),
        jd_text=body,
        jd_quality=quality,
    )
    store = AsyncMock()
    store.get_jobs_by_canonical_ids.return_value = {"1": old}
    bridge = AsyncMock()

    async def scan(**kwargs):
        result = await kwargs["on_discovery"]([{"id": "1"}])
        assert result == {"skip_ids": [], "reused_jobs": []}
        return []

    bridge.run_scan.side_effect = scan
    source = JobboardExtensionSource(
        source="linkedin",
        store=store,
        bridge=bridge,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["SWE"]),
    )
    await source.fetch_jobs({})
