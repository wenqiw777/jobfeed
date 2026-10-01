from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

import jobfeed.adapters.sources._linkedin_discover as module
from jobfeed.adapters.sources._linkedin_guest_http import GuestResponse
from jobfeed.adapters.sources.jobboard_extension import JobboardExtensionSource
from jobfeed.adapters.sources.linkedin_guest import (
    GuestSourceSettings,
    LinkedInGuestSource,
)
from jobfeed.config_sources import SourcesBoardExtensionConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.ports.source import PartialSourceFetchError
from jobfeed.services.jobright_bridge import JobrightBridgeError
from tests.unit.test_intermediary_resolution import AUDITED_PUBLISHERS


def source(bridge, store=None, platform="linkedin"):
    return JobboardExtensionSource(
        source=platform,
        bridge=bridge,
        store=store,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["SWE"]),
    )


@pytest.mark.parametrize(
    "company",
    [
        "Jobright.ai",
        "Jobright",
        "Jobwright",
        " YARA  AI ",
        "Jobs via Dice",
        "DICE",
        "RemoteHunter",
        "Remote Hunter",
        "Torentify",
        *AUDITED_PUBLISHERS,
    ],
)
async def test_blocked_discovery_skips_detail_and_cache_lookup(company):
    bridge, store = AsyncMock(), AsyncMock()
    store.get_jobs_by_canonical_ids.return_value = {}

    async def scan(**kwargs):
        decision = await kwargs["on_discovery"](
            [{"id": "1", "employer": {"name": company}}]
        )
        assert decision == {"skip_ids": ["1"], "reused_jobs": []}
        return []

    bridge.run_scan.side_effect = scan
    assert await source(bridge, store).fetch_jobs({}) == []
    store.get_jobs_by_canonical_ids.assert_not_awaited()


@pytest.mark.parametrize(
    "company", ["Unknown", "Yara International", "Dice Therapeutics", "Acme"]
)
async def test_other_employers_are_not_blocked(company):
    bridge = AsyncMock()

    async def scan(**kwargs):
        decision = await kwargs["on_discovery"](
            [{"id": "1", "employer": {"name": company}}]
        )
        assert decision == {"skip_ids": [], "reused_jobs": []}
        return []

    bridge.run_scan.side_effect = scan
    await source(bridge).fetch_jobs({})


async def test_blocked_cached_company_is_not_reused_when_card_has_no_company():
    bridge, store = AsyncMock(), AsyncMock()
    store.get_jobs_by_canonical_ids.return_value = {
        "1": JobPosting(
            platform="linkedin",
            canonical_id="1",
            title="SWE",
            company="Jobright.ai",
            location="US",
            url="https://example.com/1",
            discovered_at=datetime.now(UTC),
            jd_text="Saved JD",
            jd_quality=QualityBand.FULL,
        )
    }

    async def scan(**kwargs):
        decision = await kwargs["on_discovery"]([{"id": "1"}])
        assert decision == {"skip_ids": ["1"], "reused_jobs": []}
        return decision["reused_jobs"]

    bridge.run_scan.side_effect = scan
    assert await source(bridge, store).fetch_jobs({}) == []


@pytest.mark.parametrize("partial", [False, True])
async def test_late_company_resolution_is_filtered_even_on_partial_failure(partial):
    bridge = AsyncMock()
    rows = [
        {
            "source": "linkedin",
            "id": "1",
            "title": "SWE",
            "url": "https://example.com/1",
            "employer": {"name": "Jobs via Dice"},
        }
    ]
    if partial:
        bridge.run_scan.side_effect = JobrightBridgeError(
            "interrupted", partial_jobs=rows
        )
        with pytest.raises(PartialSourceFetchError) as error:
            await source(bridge).fetch_jobs({})
        assert error.value.postings == []
    else:
        bridge.run_scan.return_value = rows
        assert await source(bridge).fetch_jobs({}) == []


async def test_guest_blocked_page_keeps_paginating_without_consuming_quota():

    def card(company, job_id):
        return (
            f'<div class="base-search-card"><a class="base-card__full-link" '
            f'href="https://www.linkedin.com/jobs/view/{job_id}"><h3>SWE</h3></a>'
            f"<h4>{company}</h4></div>"
        )

    fetch = AsyncMock(
        side_effect=[
            GuestResponse(status=200, text=card("Jobs via Dice", "1")),
            GuestResponse(status=200, text=card("Acme", "2")),
        ]
    )
    guest = LinkedInGuestSource(
        settings=GuestSourceSettings(
            search_urls=["https://www.linkedin.com/jobs/search/?keywords=SWE"],
            max_jobs=1,
        ),
        fetcher=fetch,
        sleeper=AsyncMock(),
        logger=Mock(),
    )
    jobs = await guest.fetch_jobs({})
    assert [job.company for job in jobs] == ["Acme"]
    assert "start=1" in fetch.call_args_list[1].args[0]


async def test_browser_skips_blocked_card_before_clicking(monkeypatch):

    monkeypatch.setattr(
        module,
        "read_first_attr",
        AsyncMock(return_value="https://www.linkedin.com/jobs/view/1"),
    )
    monkeypatch.setattr(
        module, "read_first_text", AsyncMock(side_effect=["SWE", "Yara AI", "US"])
    )
    monkeypatch.setattr(module, "read_job_description", AsyncMock(return_value=""))
    card = AsyncMock()
    card.get_attribute.return_value = "1"
    assert (
        await module._posting_from_card(
            AsyncMock(), card, "https://www.linkedin.com/jobs/search/"
        )
        is None
    )
    card.click.assert_not_awaited()
