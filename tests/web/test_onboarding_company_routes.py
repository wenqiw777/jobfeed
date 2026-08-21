"""HTTP contract for profile-derived company recommendations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from jobfeed.onboarding_companies import (
    CatalogCompany,
    CompanyCatalogState,
    CompanyRecommendation,
    CompanyRecommendationStore,
    OnboardingCompanyService,
)
from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState
from jobfeed.onboarding_types import ProviderOnboardingState
from jobfeed.web.app import create_web_app

HTTP_OK = 200


class FakeRecommender:
    """Return candidates without touching a real provider."""

    async def recommend_companies(
        self,
        provider: str,
        model: str,
        profile: JobProfile,
    ) -> list[CompanyRecommendation]:
        return [
            CompanyRecommendation(
                name="Acme",
                slug="acme",
                rationale=f"{profile.desired_titles[0]} at {provider}/{model}",
            )
        ]


@asynccontextmanager
async def _open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


async def test_recommendation_route_returns_resumable_candidates(
    tmp_path: Path,
) -> None:
    """The browser can request provider-backed candidates after confirmation."""
    app = create_web_app()
    app.state.onboarding_company_service = OnboardingCompanyService(
        store=CompanyRecommendationStore(
            tmp_path / "data" / "onboarding-companies.json"
        ),
        recommender=FakeRecommender(),
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

    async with _open_client(app) as client:
        recommended = await client.post("/api/onboarding/companies/recommend")
        resumed = await client.post("/api/onboarding/companies/recommend")

    assert recommended.status_code == HTTP_OK, recommended.text
    assert recommended.json()["recommendations"][0]["slug"] == "acme"
    assert resumed.json() == recommended.json()


async def test_public_company_catalog_returns_canonical_vendor_rows() -> None:
    """The onboarding page can load a broad real-ATS catalog in one request."""
    app = create_web_app()

    async def load_catalog() -> CompanyCatalogState:
        return CompanyCatalogState(
            source_counts={"fixture": 2},
            companies=[
                CatalogCompany(slug="acme", vendor="greenhouse"),
                CatalogCompany(slug="beta", vendor="ashby"),
            ],
        )

    app.state.onboarding_company_catalog = load_catalog

    async with _open_client(app) as client:
        response = await client.get("/api/onboarding/companies/catalog")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {
        "source_counts": {"fixture": 2},
        "companies": [
            {"slug": "acme", "vendor": "greenhouse"},
            {"slug": "beta", "vendor": "ashby"},
        ],
    }


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
