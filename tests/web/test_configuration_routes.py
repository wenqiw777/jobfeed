"""Web contracts for first-run GUI configuration."""

from __future__ import annotations

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
        current["sources"]["indeed"].update({"enabled": True, "search_urls": []})
        response = await client.put("/api/config", json=current)

    assert response.status_code == HTTP_VALIDATION_ERROR
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == original
