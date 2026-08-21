"""HTTP contracts for résumé upload, analysis, and profile confirmation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from jobfeed.onboarding_resume import (
    ResumeDraftStore,
    ResumeFileStore,
    ResumeOnboardingService,
)
from jobfeed.onboarding_resume_types import JobProfile
from jobfeed.onboarding_types import ProviderOnboardingState
from jobfeed.web.app import create_web_app

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422


class FakeAnalyzer:
    """Return a deterministic complete profile for HTTP tests."""

    async def analyze(
        self,
        provider: str,
        model: str,
        resume_text: str,
    ) -> JobProfile:
        """Return profile fields while asserting the selected model context."""
        assert (provider, model) == ("codex_cli", "gpt-5.6-sol")
        assert "Python" in resume_text
        return _profile()


@asynccontextmanager
async def _open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


async def test_resume_flow_uploads_analyzes_edits_and_resumes(
    tmp_path: Path,
) -> None:
    """The HTTP flow returns no absolute path and preserves user confirmation."""
    (tmp_path / "data").mkdir()
    app = create_web_app()
    app.state.onboarding_resume_service = ResumeOnboardingService(
        files=ResumeFileStore(tmp_path / "data" / "resumes"),
        drafts=ResumeDraftStore(tmp_path / "data" / "onboarding-resume.json"),
        analyzer=FakeAnalyzer(),
        provider_state=lambda: ProviderOnboardingState(
            provider="codex_cli",
            connected=True,
            detailed_model="gpt-5.6-sol",
        ),
    )

    async with _open_client(app) as client:
        uploaded = await client.post(
            "/api/onboarding/resume",
            files={"file": ("candidate.md", b"Python engineer", "text/markdown")},
        )
        analyzed = await client.post("/api/onboarding/resume/analyze")
        body = analyzed.json()["profile"]
        body["desired_titles"] = ["Staff Platform Engineer"]
        confirmed = await client.put("/api/onboarding/profile", json=body)
        resumed = await client.get("/api/onboarding/resume")

    assert uploaded.status_code == HTTP_OK, uploaded.text
    assert uploaded.json()["original_name"] == "candidate.md"
    assert "Python engineer" in uploaded.json()["extracted_text"]
    assert str(tmp_path) not in uploaded.text
    assert analyzed.status_code == HTTP_OK, analyzed.text
    assert confirmed.status_code == HTTP_OK, confirmed.text
    assert confirmed.json()["is_confirmed"] is True
    assert resumed.json()["profile"]["desired_titles"] == ["Staff Platform Engineer"]


async def test_resume_upload_rejects_unsupported_file(tmp_path: Path) -> None:
    """Invalid uploads use the shared API error envelope."""
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        response = await client.post(
            "/api/onboarding/resume",
            files={"file": ("resume.rtf", b"resume", "application/rtf")},
        )

    assert response.status_code == HTTP_UNPROCESSABLE
    assert response.json()["error"]["code"] == "invalid_resume"


def _profile() -> JobProfile:
    return JobProfile(
        desired_titles=["Platform Engineer"],
        seniority_levels=["Senior"],
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
