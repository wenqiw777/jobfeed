"""HTTP contracts for generated and user-selected onboarding searches."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState
from jobfeed.onboarding_searches import OnboardingSearchService, OnboardingSearchStore
from jobfeed.web.app import create_web_app

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
EXPECTED_GENERATED_SEARCHES = 2


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


async def test_searches_generate_and_save_selection(tmp_path: Path) -> None:
    """The API resumes generated searches and the user's deselection."""
    app = create_web_app()
    app.state.onboarding_search_service = OnboardingSearchService(
        store=OnboardingSearchStore(tmp_path / "data" / "onboarding-searches.json"),
        resume_state=lambda: ResumeDraftState(
            extracted_text="resume",
            profile=_profile(),
            is_confirmed=True,
        ),
    )

    async with _open_client(app) as client:
        generated = await client.get("/api/onboarding/searches")
        body = generated.json()
        body["searches"][0]["enabled"] = False
        saved = await client.put(
            "/api/onboarding/searches",
            json={"searches": body["searches"]},
        )
        resumed = await client.get("/api/onboarding/searches")

    assert generated.status_code == HTTP_OK, generated.text
    assert len(body["searches"]) == EXPECTED_GENERATED_SEARCHES
    assert saved.status_code == HTTP_OK, saved.text
    assert resumed.json()["searches"][0]["enabled"] is False


async def test_searches_reject_wrong_source_url(tmp_path: Path) -> None:
    """Source URL validation uses the shared API error envelope."""
    app = create_web_app()
    app.state.onboarding_search_service = OnboardingSearchService(
        store=OnboardingSearchStore(tmp_path / "data" / "onboarding-searches.json"),
        resume_state=lambda: ResumeDraftState(
            extracted_text="resume",
            profile=_profile(),
            is_confirmed=True,
        ),
    )

    async with _open_client(app) as client:
        response = await client.put(
            "/api/onboarding/searches",
            json={
                "searches": [
                    {
                        "id": "manual-1",
                        "source": "indeed",
                        "query": "Platform Engineer",
                        "location": "New York, NY",
                        "url": "https://evil.example/jobs?q=platform",
                        "enabled": True,
                    }
                ]
            },
        )

    assert response.status_code == HTTP_UNPROCESSABLE


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
