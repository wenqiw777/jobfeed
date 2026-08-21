"""Representative onboarding calibration uses confirmed Indeed search JDs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.models import JobPosting
from jobfeed.onboarding_calibration_job import OnboardingCalibrationJobSampler
from jobfeed.onboarding_searches import SearchDraftState, SearchSuggestion

SAMPLE_LIMIT = 30


def _search(
    id_: str,
    *,
    source: str = "indeed",
    enabled: bool = True,
) -> SearchSuggestion:
    if source == "indeed":
        url = f"https://www.indeed.com/jobs?q={id_}"
    else:
        url = f"https://www.linkedin.com/jobs/search/?keywords={id_}"
    return SearchSuggestion(
        id=id_,
        source=source,
        query=id_,
        location="Austin, TX",
        url=url,
        enabled=enabled,
    )


def _posting(id_: str, length: int, *, title: str | None = None) -> JobPosting:
    return JobPosting(
        platform="indeed",
        canonical_id=id_,
        url=f"https://www.indeed.com/viewjob?jk={id_}",
        title=title or f"Software Engineer {id_}",
        company="Real Company",
        location="Austin, TX",
        discovered_at=datetime(2026, 8, 16, tzinfo=UTC),
        jd_text="x" * length,
    )


@pytest.mark.asyncio
async def test_sampler_uses_enabled_indeed_searches_and_mean_jd_length() -> None:
    state = SearchDraftState(
        profile_fingerprint="confirmed-profile",
        searches=[
            _search("software engineer"),
            _search("platform", enabled=False),
            _search("linkedin", source="linkedin_guest"),
        ],
    )
    calls: list[tuple[list[str], int]] = []

    async def fetch_indeed(urls: list[str], limit: int) -> list[JobPosting]:
        calls.append((urls, limit))
        return [
            _posting("short", 100),
            _posting("representative", 200),
            _posting("long", 400),
        ]

    sampler = OnboardingCalibrationJobSampler(
        search_state=lambda: state,
        fetch_indeed=fetch_indeed,
    )

    sample = await sampler.sample()

    assert calls == [
        (["https://www.indeed.com/jobs?q=software engineer"], SAMPLE_LIMIT)
    ]
    assert sample is not None
    assert sample.id == "representative"
    assert sample.jd_text == "x" * 200


@pytest.mark.asyncio
async def test_sampler_limits_the_representative_pool_to_thirty_full_jds() -> None:
    state = SearchDraftState(
        profile_fingerprint="confirmed-profile",
        searches=[_search("software engineer")],
    )

    async def fetch_indeed(_urls: list[str], limit: int) -> list[JobPosting]:
        assert limit == SAMPLE_LIMIT
        return [_posting(str(index), 100 + index) for index in range(SAMPLE_LIMIT + 1)]

    sampler = OnboardingCalibrationJobSampler(
        search_state=lambda: state,
        fetch_indeed=fetch_indeed,
    )

    sample = await sampler.sample()

    assert sample is not None
    assert sample.id in {"14", "15"}
    assert sample.id != "30"


@pytest.mark.asyncio
async def test_sampler_does_not_fabricate_without_a_full_indeed_jd() -> None:
    state = SearchDraftState(
        profile_fingerprint="confirmed-profile",
        searches=[_search("software engineer")],
    )

    async def fetch_indeed(_urls: list[str], _limit: int) -> list[JobPosting]:
        missing = _posting("missing", 100)
        missing.jd_text = None
        return [missing]

    sampler = OnboardingCalibrationJobSampler(
        search_state=lambda: state,
        fetch_indeed=fetch_indeed,
    )

    assert await sampler.sample() is None


@pytest.mark.asyncio
async def test_sampler_rejects_obviously_unrelated_indeed_noise() -> None:
    state = SearchDraftState(
        profile_fingerprint="confirmed-profile",
        searches=[_search("Software Engineer Intern")],
    )

    async def fetch_indeed(_urls: list[str], _limit: int) -> list[JobPosting]:
        return [
            _posting("software", 100, title="Backend Software Engineer Intern"),
            _posting("nursing", 400, title="RN Labor and Delivery"),
            _posting("platform", 900, title="Platform Engineer"),
        ]

    sampler = OnboardingCalibrationJobSampler(
        search_state=lambda: state,
        fetch_indeed=fetch_indeed,
    )

    sample = await sampler.sample()

    assert sample is not None
    assert sample.id == "software"
