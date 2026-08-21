"""Web contracts for gradual personal relevance learning."""

# ruff: noqa: PLR2004

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI

from jobfeed.domain.models import JobPosting, QualityBand, StageAResult
from jobfeed.personal_ml_learning import PersonalMLStatus
from jobfeed.web.app import create_web_app


@asynccontextmanager
async def _open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


async def test_learning_status_counts_persisted_quick_teacher_labels(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        store = app.state.context["store"]
        for index, score in enumerate((80, 40, 75), start=1):
            saved = await store.save_job(
                JobPosting(
                    platform="indeed",
                    canonical_id=f"learning-{index}",
                    url=f"https://example.com/{index}",
                    title="Backend Engineer",
                    company="Example",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Backend platform engineering role. " * 20,
                    jd_quality=QualityBand.FULL,
                )
            )
            await store.save_stage_a(
                saved.job_id,
                StageAResult(
                    score=score,
                    one_line="Teacher label",
                    timing_eligible="yes",
                    model="quick-model",
                    prompt_hash="prompt",
                    resume_hash="resume",
                ),
            )

        response = await client.get("/api/personal-ml/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "collecting",
        "label_count": 3,
        "ranking_count": 0,
        "shadow_count": 0,
        "next_target": 100,
        "model_threshold": None,
        "quick_pass_recall": None,
        "quick_fail_rejection": None,
        "category_recall": None,
        "baseline_rejection": None,
        "estimated_call_reduction": None,
        "rolling_recall": None,
    }


class ReadyLearningService:
    """Expose one validated threshold for explicit activation."""

    async def status(self, **_kwargs: object) -> PersonalMLStatus:
        return PersonalMLStatus(
            state="ready",
            label_count=500,
            ranking_count=200,
            shadow_count=200,
            next_target=None,
            model_threshold=0.73,
            quick_pass_recall=0.96,
            quick_fail_rejection=0.44,
            category_recall=0.92,
            baseline_rejection=0.08,
            estimated_call_reduction=0.31,
            rolling_recall=0.96,
        )


async def test_activation_persists_validated_user_threshold_only_after_ready(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    app.state.personal_ml_service = ReadyLearningService()

    async with _open_client(app) as client:
        response = await client.post("/api/personal-ml/activate")

    assert response.status_code == 200
    body = response.json()
    assert body["scoring"]["ml_gate_enabled"] is True
    assert body["ml_gate"]["threshold_override"] == 0.73


async def test_general_settings_can_manually_enable_packaged_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()

    async with _open_client(app) as client:
        configuration = (await client.get("/api/config")).json()
        configuration.pop("configured")
        configuration["scoring"]["ml_gate_enabled"] = True
        response = await client.put("/api/config", json=configuration)

    assert response.status_code == 200
    body = response.json()
    assert body["scoring"]["ml_gate_enabled"] is True
    assert body["ml_gate"]["threshold_override"] is None
