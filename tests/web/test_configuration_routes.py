"""Web contracts for first-run GUI configuration."""

from __future__ import annotations

import json
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from jobfeed.web.app import create_web_app

HTTP_OK = 200
HTTP_VALIDATION_ERROR = 422
SAVED_SCORE_THRESHOLD = 73
SAVED_DAILY_CALLS = 42


@asynccontextmanager
async def _open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


async def test_fresh_runtime_exposes_defaults_without_creating_config(
    tmp_path, monkeypatch
) -> None:
    """GET reports onboarding until the user explicitly saves settings."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        response = await client.get("/api/config")

    assert response.status_code == HTTP_OK
    body = response.json()
    assert body["configured"] is False
    assert body["llm"]["master_resume_path"] == "resume.example.md"
    assert body["sources"]["ats"]["enabled"] is True
    assert "db" not in body
    assert not (tmp_path / "config.toml").exists()


async def test_configuration_exposes_selected_model_performance(
    tmp_path, monkeypatch
) -> None:
    """GET pairs the selected model version with its committed metrics."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    model_dir = tmp_path / "models" / "ml_gate"
    model_dir.mkdir(parents=True)
    (model_dir / "v20260601T170453Z.meta.json").write_text(
        json.dumps(
            {
                "version": "v20260601T170453Z",
                "threshold": 0.19,
                "recall_pos": 0.9730377471539844,
                "precision_pos": 0.5438713998660415,
                "f1": 0.6977443609022557,
                "neg_blocked_pct": 0.7446090380648791,
                "train_size": 7002,
            }
        ),
        encoding="utf-8",
    )
    app = create_web_app()

    async with _open_client(app) as client:
        response = await client.get("/api/config")

    assert response.status_code == HTTP_OK
    assert response.json()["ml_gate_performance"] == {
        "threshold": 0.19,
        "recall": 0.9730377471539844,
        "precision": 0.5438713998660415,
        "f1": 0.6977443609022557,
        "irrelevant_rejection": 0.7446090380648791,
        "training_jobs": 7002,
    }


async def test_save_configuration_is_persisted_and_applied_without_restart(
    tmp_path, monkeypatch
) -> None:
    """PUT atomically stores valid settings and updates the live context."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        current = (await client.get("/api/config")).json()
        current.pop("configured")
        current.pop("ml_gate_performance")
        current["llm"].update(
            {
                "master_resume_path": "data/my-resume.md",
                "stage_a": "mock/onboarding-a",
                "stage_b": "mock/onboarding-b",
                "max_daily_score_calls": SAVED_DAILY_CALLS,
            }
        )
        current["scoring"]["stage_a_threshold"] = SAVED_SCORE_THRESHOLD
        current["sources"]["ats"]["seed_companies"] = ["stripe", "openai"]
        current["sources"]["linkedin_guest"].update(
            {
                "enabled": True,
                "search_urls": [
                    "https://www.linkedin.com/jobs/search/?keywords=python"
                ],
            }
        )
        response = await client.put("/api/config", json=current)

    assert response.status_code == HTTP_OK, response.text
    assert response.json()["configured"] is True
    saved = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert saved["llm"]["master_resume_path"] == "data/my-resume.md"
    assert saved["scoring"]["stage_a_threshold"] == SAVED_SCORE_THRESHOLD
    assert saved["sources"]["ats"]["seed_companies"] == ["stripe", "openai"]
    assert app.state.context["settings"].llm.max_daily_score_calls == SAVED_DAILY_CALLS
    assert app.state.context["settings"].sources.linkedin_guest.enabled is True
    assert not list(tmp_path.glob(".config.toml.*"))


async def test_invalid_enabled_source_is_rejected_without_overwriting_config(
    tmp_path, monkeypatch
) -> None:
    """Pydantic source guards reject incomplete GUI submissions."""
    monkeypatch.chdir(tmp_path)
    original = "[db]\npath = 'data/jobfeed.sqlite'\n"
    (tmp_path / "config.toml").write_text(original, encoding="utf-8")
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        current = (await client.get("/api/config")).json()
        current.pop("configured")
        current.pop("ml_gate_performance")
        current["sources"]["indeed"].update({"enabled": True, "search_urls": []})
        response = await client.put("/api/config", json=current)

    assert response.status_code == HTTP_VALIDATION_ERROR
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == original
