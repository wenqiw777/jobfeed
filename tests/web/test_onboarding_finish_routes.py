"""Web contracts for applying the completed onboarding draft."""

from __future__ import annotations

import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from jobfeed.onboarding_plan_usage import PlanUsageSnapshot
from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState
from jobfeed.onboarding_searches import SearchDraftState, SearchSuggestion
from jobfeed.onboarding_types import ProviderOnboardingState
from jobfeed.web.app import create_web_app

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
SAVED_DAILY_CALLS = 70


class StateService:
    """Return one injected onboarding state through the shared dependency API."""

    def __init__(self, state: object) -> None:
        self._state = state

    def state(self) -> object:
        return self._state


class PlanUsageReader:
    """Return a deterministic live Codex allowance snapshot."""

    async def read(self) -> PlanUsageSnapshot:
        return PlanUsageSnapshot(
            plan_name="Pro",
            used_percent=35,
            remaining_percent=65,
            window_minutes=10_080,
            resets_at=1_787_196_565,
        )


class EvaluationCalibrator:
    """Return deterministic measured usage for one Quick + Detailed pair."""

    def __init__(self) -> None:
        self.job_description: str | None = None

    async def calibrate(self, job_description: str) -> SimpleNamespace:
        self.job_description = job_description
        return SimpleNamespace(
            quick=SimpleNamespace(
                model="gpt-5.6-luna",
                input_tokens=1_000,
                output_tokens=100,
                cost_usd=0.002,
                latency_ms=1_500,
            ),
            detailed=SimpleNamespace(
                model="gpt-5.6-terra",
                input_tokens=3_000,
                output_tokens=500,
                cost_usd=0.01,
                latency_ms=4_500,
            ),
            allowance_before_percent=35,
            allowance_after_percent=35,
        )


class CalibrationJobSampler:
    """Return one representative real Indeed posting."""

    def __init__(self) -> None:
        self.calls = 0

    async def sample(self) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            id="indeed-42",
            title="Platform Engineer",
            company="Real Systems",
            url="https://www.indeed.com/viewjob?jk=indeed-42",
            jd_text="A representative real Indeed job description. " * 4,
        )


@asynccontextmanager
async def _open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


def _profile() -> JobProfile:
    return JobProfile(
        desired_titles=["Platform Engineer"],
        seniority_levels=["Senior"],
        target_countries=["United States"],
        target_locations=["New York, NY"],
        work_modes=["hybrid"],
        industries=["Developer tools"],
        company_sizes=["mid-size"],
        work_authorization="Authorized",
        hiring_timeline="Available now",
        excluded_titles=[],
        excluded_companies=[],
        excluded_locations=[],
        excluded_keywords=[],
        maximum_posting_age_days=14,
        resume_evidence=["Built distributed systems"],
    )


def _wire_ready_drafts(app: FastAPI) -> None:
    app.state.onboarding_provider_service = StateService(
        ProviderOnboardingState(
            provider="codex_cli",
            connected=True,
            quick_model="gpt-5.6-luna",
            detailed_model="gpt-5.6-terra",
        )
    )
    app.state.onboarding_resume_service = StateService(
        ResumeDraftState(
            stored_name="abc123-resume.pdf",
            original_name="resume.pdf",
            extracted_text="Platform engineer",
            profile=_profile(),
            is_confirmed=True,
        )
    )
    app.state.onboarding_search_service = StateService(
        SearchDraftState(
            profile_fingerprint="profile-ready",
            searches=[
                SearchSuggestion(
                    id="linkedin-one",
                    source="linkedin_guest",
                    query="Platform Engineer",
                    location="New York, NY",
                    url="https://www.linkedin.com/jobs/search/?keywords=platform",
                    enabled=True,
                ),
                SearchSuggestion(
                    id="indeed-one",
                    source="indeed",
                    query="Platform Engineer",
                    location="New York, NY",
                    url="https://www.indeed.com/jobs?q=platform",
                    enabled=True,
                ),
            ],
        )
    )


async def test_finish_applies_models_resume_and_selected_searches(
    tmp_path: Path, monkeypatch
) -> None:
    """Finish saves the active config only after all draft prerequisites exist."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    _wire_ready_drafts(app)

    async with _open_client(app) as client:
        configuration = (await client.get("/api/config")).json()
        configuration.pop("configured")
        configuration.pop("ml_gate_performance")
        configuration["llm"]["max_daily_score_calls"] = SAVED_DAILY_CALLS
        configuration["scoring"]["ml_gate_enabled"] = True
        response = await client.post(
            "/api/onboarding/finish",
            json={
                "configuration": configuration,
                "expected_jobs": 80,
            },
        )

    assert response.status_code == HTTP_OK, response.text
    assert response.json()["configured"] is True
    saved = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert saved["llm"]["stage_a"] == "codex-cli/gpt-5.6-luna"
    assert saved["llm"]["stage_b"] == "codex-cli/gpt-5.6-terra"
    assert saved["llm"]["master_resume_path"] == "data/resumes/abc123-resume.pdf"
    assert saved["llm"]["max_daily_score_calls"] == SAVED_DAILY_CALLS
    assert saved["scoring"]["ml_gate_enabled"] is False
    assert saved["sources"]["linkedin_guest"]["enabled"] is True
    assert saved["sources"]["indeed"]["enabled"] is True
    assert len(saved["sources"]["linkedin_guest"]["search_urls"]) == 1
    assert len(saved["sources"]["indeed"]["search_urls"]) == 1
    assert saved["sources"]["ats"]["title_keywords"] == ["Platform Engineer"]


async def test_finish_applies_bedrock_models_region_and_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """Bedrock connection settings become the active evaluation backend."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    _wire_ready_drafts(app)
    app.state.onboarding_provider_service = StateService(
        ProviderOnboardingState(
            provider="amazon_bedrock",
            connected=True,
            quick_model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            detailed_model="us.anthropic.claude-sonnet-5",
            region="us-east-1",
            profile="jobfeed",
        )
    )

    async with _open_client(app) as client:
        configuration = (await client.get("/api/config")).json()
        configuration.pop("configured")
        configuration.pop("ml_gate_performance")
        response = await client.post(
            "/api/onboarding/finish",
            json={"configuration": configuration, "expected_jobs": 80},
        )

    assert response.status_code == HTTP_OK, response.text
    saved = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert saved["llm"]["stage_a"] == (
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    assert saved["llm"]["stage_b"] == "bedrock/us.anthropic.claude-sonnet-5"
    assert saved["llm"]["bedrock_region"] == "us-east-1"
    assert saved["llm"]["bedrock_profile"] == "jobfeed"


async def test_finish_rejects_incomplete_draft_without_creating_config(
    tmp_path: Path, monkeypatch
) -> None:
    """An incomplete final step cannot partially mark the workspace configured."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        configuration = (await client.get("/api/config")).json()
        configuration.pop("configured")
        configuration.pop("ml_gate_performance")
        response = await client.post(
            "/api/onboarding/finish",
            json={
                "configuration": configuration,
                "expected_jobs": 100,
            },
        )

    assert response.status_code == HTTP_UNPROCESSABLE
    assert response.json()["error"]["code"] == "onboarding_incomplete"
    assert not (tmp_path / "config.toml").exists()


async def test_plan_usage_returns_live_codex_allowance(
    tmp_path: Path, monkeypatch
) -> None:
    """Review can compare planned calls with the signed-in Codex plan window."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    _wire_ready_drafts(app)
    app.state.onboarding_plan_usage_reader = PlanUsageReader()

    async with _open_client(app) as client:
        response = await client.get("/api/onboarding/plan-usage")

    assert response.status_code == HTTP_OK, response.text
    assert response.json() == {
        "provider": "codex_cli",
        "source": "live",
        "plan_name": "Pro",
        "used_percent": 35,
        "remaining_percent": 65,
        "window_minutes": 10_080,
        "resets_at": 1_787_196_565,
        "detail": "Live Codex allowance from this signed-in account.",
    }


async def test_evaluation_calibration_returns_measured_tokens_cost_and_allowance(
    tmp_path: Path, monkeypatch
) -> None:
    """Calibration measures both production evaluation stages for one JD."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    _wire_ready_drafts(app)
    calibrator = EvaluationCalibrator()
    app.state.onboarding_evaluation_calibrator = calibrator

    sample = "Representative platform engineering job description. " * 4
    async with _open_client(app) as client:
        response = await client.post(
            "/api/onboarding/evaluation-calibration",
            json={"job_description": sample},
        )

    assert response.status_code == HTTP_OK, response.text
    assert calibrator.job_description == sample
    assert response.json() == {
        "quick": {
            "model": "gpt-5.6-luna",
            "input_tokens": 1_000,
            "output_tokens": 100,
            "cost_usd": 0.002,
            "latency_ms": 1_500,
        },
        "detailed": {
            "model": "gpt-5.6-terra",
            "input_tokens": 3_000,
            "output_tokens": 500,
            "cost_usd": 0.01,
            "latency_ms": 4_500,
        },
        "allowance_before_percent": 35,
        "allowance_after_percent": 35,
        "allowance_resolution_percent": 1,
    }


async def test_calibration_job_returns_a_representative_real_indeed_posting(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    _wire_ready_drafts(app)
    sampler = CalibrationJobSampler()
    app.state.onboarding_calibration_job_sampler = sampler

    async with _open_client(app) as client:
        response = await client.get("/api/onboarding/calibration-job")

    assert response.status_code == HTTP_OK, response.text
    assert sampler.calls == 1
    assert response.json() == {
        "id": "indeed-42",
        "title": "Platform Engineer",
        "company": "Real Systems",
        "url": "https://www.indeed.com/viewjob?jk=indeed-42",
        "jd_text": "A representative real Indeed job description. " * 4,
    }
