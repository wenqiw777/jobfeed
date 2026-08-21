"""Deterministic onboarding search suggestions and validation."""

from pathlib import Path

import pytest

from jobfeed.onboarding_resume_types import JobProfile
from jobfeed.onboarding_searches import (
    OnboardingSearchStore,
    SearchSuggestion,
    generate_search_suggestions,
)

PRIVATE_FILE_MODE = 0o600
EXPECTED_ALL_SEARCHES = 12


def _profile() -> JobProfile:
    return JobProfile(
        desired_titles=["Platform Engineer"],
        seniority_levels=["Entry level"],
        target_countries=["United States"],
        target_locations=["New York, NY"],
        work_modes=["remote", "hybrid"],
        industries=["Developer tools"],
        company_sizes=["mid-size"],
        work_authorization="Authorized in the US",
        hiring_timeline="Available now",
        excluded_titles=[],
        excluded_companies=[],
        excluded_locations=[],
        excluded_keywords=[],
        maximum_posting_age_days=14,
        resume_evidence=["Built Python systems"],
    )


def test_confirmed_profile_generates_deterministic_search_urls() -> None:
    """Every role uses one nationwide US search on both supported sources."""
    suggestions = generate_search_suggestions(_profile())

    assert [(item.source, item.query, item.location) for item in suggestions] == [
        ("linkedin_guest", "Platform Engineer", "United States"),
        ("indeed", "Platform Engineer", "United States"),
    ]
    assert suggestions[0].url == (
        "https://www.linkedin.com/jobs/search/?keywords=Platform+Engineer"
        "&location=United+States&f_TPR=r1209600"
    )
    assert suggestions[1].url == (
        "https://www.indeed.com/jobs?q=Platform+Engineer&l=United+States&fromage=14"
    )


def test_search_suggestions_show_six_unranked_titles_and_ignore_cities() -> None:
    """Cities never multiply choices and no role is silently preselected."""
    profile = _profile().model_copy(
        update={
            "desired_titles": [
                "Platform Engineer",
                "Backend Engineer",
                "Software Engineer",
                "Site Reliability Engineer",
                "Data Engineer",
                "ML Engineer",
                "Cloud Engineer",
            ],
            "target_locations": ["New York, NY", "Boston, MA"],
        }
    )

    suggestions = generate_search_suggestions(profile)

    assert len(suggestions) == EXPECTED_ALL_SEARCHES
    assert not any(item.enabled for item in suggestions)
    assert suggestions[-1].query == "ML Engineer"
    assert suggestions[-1].location == "United States"
    assert suggestions[-1].enabled is False


def test_search_draft_rejects_a_url_for_the_wrong_source() -> None:
    """A pasted URL cannot masquerade as a supported search source."""
    with pytest.raises(ValueError, match="LinkedIn search URL"):
        SearchSuggestion(
            id="manual-1",
            source="linkedin_guest",
            query="Platform Engineer",
            location="New York, NY",
            url="https://evil.example/jobs?q=platform",
            enabled=True,
        )


def test_selected_searches_round_trip_privately(tmp_path: Path) -> None:
    """Edited and deselected searches resume from the local draft."""
    store = OnboardingSearchStore(tmp_path / "data" / "onboarding-searches.json")
    searches = generate_search_suggestions(_profile())
    searches[0] = searches[0].model_copy(update={"enabled": False})

    saved = store.save(searches, _profile())

    assert saved.searches[0].enabled is False
    assert store.load().searches == saved.searches
    mode = (tmp_path / "data" / "onboarding-searches.json").stat().st_mode
    assert mode & 0o777 == PRIVATE_FILE_MODE
