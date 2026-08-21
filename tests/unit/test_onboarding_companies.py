"""Resumable AI company recommendations for onboarding."""

from __future__ import annotations

from pathlib import Path

from jobfeed.onboarding_companies import (
    CompanyRecommendation,
    CompanyRecommendationStore,
    OnboardingCompanyService,
)
from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState
from jobfeed.onboarding_types import ProviderOnboardingState


class FakeRecommender:
    """Return one deterministic recommendation while recording calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def recommend_companies(
        self,
        provider: str,
        model: str,
        profile: JobProfile,
    ) -> list[CompanyRecommendation]:
        self.calls += 1
        assert (provider, model) == ("codex_cli", "gpt-5.6-sol")
        assert profile.industries == ["Developer tools"]
        return [
            CompanyRecommendation(
                name="Acme",
                slug="acme",
                rationale="Strong platform engineering fit",
            )
        ]


async def test_recommendations_are_profile_bound_and_resumable(tmp_path: Path) -> None:
    """Reloading the same profile reuses the private recommendation draft."""
    recommender = FakeRecommender()
    store = CompanyRecommendationStore(tmp_path / "data" / "onboarding-companies.json")
    service = OnboardingCompanyService(
        store=store,
        recommender=recommender,
        resume_state=lambda: ResumeDraftState(
            extracted_text="resume",
            profile=_profile(),
            is_confirmed=True,
        ),
        provider_state=lambda: ProviderOnboardingState(
            provider="codex_cli",
            connected=True,
            detailed_model="gpt-5.6-sol",
        ),
    )

    first = await service.recommend()
    resumed = await service.recommend()

    assert first == resumed
    assert first.recommendations[0].slug == "acme"
    assert recommender.calls == 1
    assert store.load() == first


def _profile() -> JobProfile:
    return JobProfile(
        desired_titles=["Platform Engineer"],
        seniority_levels=["Entry level"],
        target_countries=["United States"],
        target_locations=["New York, NY"],
        work_modes=["remote"],
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
