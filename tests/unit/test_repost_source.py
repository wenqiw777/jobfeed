from dataclasses import replace
from unittest.mock import AsyncMock

from jobfeed.adapters.sources.jobboard_extension import (
    POSTING,
    JobboardExtensionSource,
    map_board_job,
)
from jobfeed.config_sources import SourcesBoardExtensionConfig
from tests.support.sqlite_jobs_evaluations import FIXED_NOW, make_job


def test_cached_jd_receives_fresh_repost_observation():
    job = replace(make_job("123"), platform="linkedin")
    mapped = map_board_job(
        {
            "source": "linkedin",
            "id": "123",
            "_stored_posting": POSTING.dump_python(job, mode="json"),
            "isRepost": True,
            "repostEvidence": "Reposted 1 day ago",
            "repostObservedAt": FIXED_NOW.isoformat(),
        },
        discovered_at=FIXED_NOW,
    )
    assert mapped.is_repost is True
    assert mapped.repost_evidence == "Reposted 1 day ago"
    assert mapped.jd_text == job.jd_text


def test_unsubstantiated_repost_flag_is_not_accepted():
    job = replace(make_job("123"), platform="linkedin")
    mapped = map_board_job(
        {
            "source": "linkedin",
            "id": "123",
            "_stored_posting": POSTING.dump_python(job, mode="json"),
            "isRepost": True,
        },
        discovered_at=FIXED_NOW,
    )
    assert mapped.is_repost is None


async def test_discovery_cache_merge_retains_fresh_evidence():
    job = replace(make_job("123"), platform="linkedin")
    store = AsyncMock()
    store.get_jobs_by_canonical_ids.return_value = {"123": job}
    bridge = AsyncMock()

    async def scan(**kwargs):
        decision = await kwargs["on_discovery"](
            [
                {
                    "id": "123",
                    "isRepost": True,
                    "repostEvidence": "Reposted 1 day ago",
                    "repostObservedAt": FIXED_NOW.isoformat(),
                }
            ]
        )
        assert decision["skip_ids"] == ["123"]
        return decision["reused_jobs"]

    bridge.run_scan.side_effect = scan
    source = JobboardExtensionSource(
        source="linkedin",
        store=store,
        bridge=bridge,
        config=SourcesBoardExtensionConfig(enabled=True, queries=["SWE"]),
    )
    [mapped] = await source.fetch_jobs({})
    assert mapped.is_repost is True
    assert mapped.jd_text == job.jd_text
