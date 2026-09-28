"""Canonical paths use real IDs and legacy paths resolve source IDs."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import aiosqlite
import pytest

from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.cli import AppContext
from jobfeed.config import Settings
from jobfeed.domain.models import (
    FitAnalysis,
    JobPosting,
    QualityBand,
    StageAResult,
    StageBResult,
    TransitionRequest,
    Verdict,
)
from jobfeed.personal_ml_learning import PersonalMLLearningService
from jobfeed.services._evaluate_canonical import current_policy_for_settings
from jobfeed.web.app import build_web_app
from tests.web.test_app_skeleton import open_client

HTTP_OK = 200
HTTP_CONFLICT = 409
HTTP_NOT_FOUND = 404
SOURCE_COUNT = 2
STAGE_B_FIT_SCORE = 93
SOURCE_AUDIT_SCORE = 30
CURRENT_QUICK_SCORE = 90


@pytest.mark.asyncio
async def test_policy_change_hides_unverified_api_scores_before_next_evaluate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy-api.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        settings = Settings()
        saved = await store.save_job(JobPosting(
            platform="linkedin", canonical_id="policy-api",
            url="https://example.test/policy-api", title="Engineer",
            company="Acme", location="Remote", discovered_at=datetime.now(UTC),
            jd_text="Build production services and APIs. " * 12,
            jd_quality=QualityBand.FULL,
        ))
        real_id = await store.resolve_real_job_id(saved.job_id)
        policy = await current_policy_for_settings(
            settings, PersonalMLLearningService(store)
        )
        a = (await store.claim_real_job_stage_a_by_ids(
            [real_id], stage_a_policy=policy.stage_a(),
            stage_b_policy=policy.stage_b(),
        ))[0]
        assert await store.save_real_job_stage_a(
            real_id, StageAResult(
                score=CURRENT_QUICK_SCORE, one_line="Fit",
                timing_eligible="yes", model="v1",
                prompt_hash="existing", resume_hash="existing",
            ), expected_revision=a.input_revision,
            expected_generation=a.claim_generation,
        )
        b = (await store.claim_real_job_stage_b_by_ids(
            [real_id], stage_a_threshold=60,
            stage_a_policy=policy.stage_a(), stage_b_policy=policy.stage_b(),
        ))[0]
        assert await store.save_real_job_stage_b(
            real_id, StageBResult(
                verdict=Verdict.APPLY, jd_summary="Fit",
                fit_analysis=FitAnalysis(score=93, strengths=[], gaps=[]),
                resume_hooks=[], model="v1", prompt_hash="existing",
                resume_hash="existing",
            ), expected_revision=b.input_revision,
            expected_generation=b.claim_generation,
        )
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "INSERT INTO evaluations(job_id,stage_a_score,stage_a_one_line,"
                "stage_a_status) VALUES(?,30,'Old source fit','completed')",
                (int(saved.job_id),),
            )
            await db.commit()
        app = build_web_app(cast(AppContext, {"store": store, "settings": settings}))
        async with open_client(app) as client:
            current = await client.get("/api/real-jobs", params={"decision": "results"})
            assert current.json()["jobs"][0]["stage_a_score"] == CURRENT_QUICK_SCORE
            settings.llm.stage_b = "detail-v2"
            stale_b = await client.get("/api/real-jobs", params={"decision": "results"})
            assert stale_b.json()["jobs"][0]["stage_a_score"] == CURRENT_QUICK_SCORE
            assert stale_b.json()["jobs"][0]["verdict"] is None
            assert stale_b.json()["jobs"][0]["evaluation_stale_reason"] == (
                "stage_b_policy_changed"
            )
            settings.llm.stage_a = "quick-v2"
            stale_a = await client.get("/api/real-jobs", params={"decision": "results"})
            row = stale_a.json()["jobs"][0]
            assert row["stage_a_score"] is None
            assert row["priority_score"] < current.json()["jobs"][0]["priority_score"]
            assert row["evaluation_stale_reason"] == "stage_a_policy_changed"
            detail = await client.get(f"/api/real-jobs/{real_id}")
            assert detail.json()["evaluation"]["stage_a"] is None
            assert detail.json()["stale_stage_a_score"] == CURRENT_QUICK_SCORE
            library = await client.get("/api/jobs", params={"canonical": "true"})
            assert library.json()["jobs"][0]["stage_a_score"] is None
            source = await client.get(f"/api/jobs/{saved.job_id}")
            assert source.json()["evaluation"]["stage_a"] is None
            audit = await client.get(f"/api/jobs/{saved.job_id}/audit")
            assert audit.json()["evaluation"]["stage_a"]["score"] == SOURCE_AUDIT_SCORE
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "UPDATE real_job_evaluations SET input_facts_json='{}' "
                    "WHERE real_job_id=?", (int(real_id),),
                )
                await db.commit()
            legacy = await client.get("/api/real-jobs", params={"decision": "results"})
            legacy_row = legacy.json()["jobs"][0]
            assert legacy_row["evaluation_stale_reason"] is None
            assert legacy_row["stage_a_score"] == CURRENT_QUICK_SCORE
            assert legacy_row["stage_b_fit_score"] == STAGE_B_FIT_SCORE
            assert legacy_row["verdict"] == "apply"
            legacy_detail = await client.get(f"/api/real-jobs/{real_id}")
            assert legacy_detail.json()["evaluation_stale_reason"] is None
            assert (
                legacy_detail.json()["evaluation"]["stage_a"]["score"]
                == CURRENT_QUICK_SCORE
            )
            legacy_library = await client.get("/api/jobs", params={"canonical": "true"})
            assert legacy_library.json()["jobs"][0]["evaluation_stale_reason"] is None
            assert (
                legacy_library.json()["jobs"][0]["stage_a_score"]
                == CURRENT_QUICK_SCORE
            )
            legacy_source = await client.get(f"/api/jobs/{saved.job_id}")
            assert legacy_source.json()["evaluation_stale_reason"] is None
            assert (
                legacy_source.json()["evaluation"]["stage_a"]["score"]
                == CURRENT_QUICK_SCORE
            )
            new_policy = await current_policy_for_settings(
                settings, PersonalMLLearningService(store)
            )
            counts = await store.canonical_policy_pending_counts(
                stage_a_policy=new_policy.stage_a(),
                stage_b_policy=new_policy.stage_b(),
            )
            assert counts == {
                "stage_a_pending": 1, "stage_b_pending": 0,
                "legacy_stage_a": 1, "legacy_stage_b": 1,
            }
            with pytest.raises(ValueError, match="legacy_stage_a=1"):
                await store.canonical_policy_cutover_ready(
                    stage_a_policy=new_policy.stage_a(),
                    stage_b_policy=new_policy.stage_b(),
                )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_results_api_uses_current_hard_filters_for_select_all(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "filter-route.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="filter-route",
                url="https://example.test/filter-route",
                title="Engineer",
                company="Acme",
                location="Seattle, WA",
                discovered_at=datetime.now(UTC),
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        async with aiosqlite.connect(tmp_path / "filter-route.db") as db:
            await db.execute(
                "UPDATE real_jobs SET identity_review_state='evaluation_conflict' "
                "WHERE id=?",
                (int(real_id),),
            )
            await db.commit()
        settings = Settings()
        app = build_web_app(cast(AppContext, {"store": store, "settings": settings}))
        async with open_client(app) as client:
            visible = await client.get("/api/real-jobs", params={"decision": "results"})
            assert visible.json()["total"] == 1
            review = await client.get(
                "/api/real-jobs",
                params={"decision": "results", "require_verdict": True},
            )
            assert review.json()["total"] == 1
            assert (
                review.json()["jobs"][0]["identity_review_state"]
                == "evaluation_conflict"
            )
            assert review.json()["jobs"][0]["stage_a_score"] is None
            selection = await client.get(
                "/api/real-jobs/selection",
                params={"decision": "results", "require_verdict": True},
            )
            assert selection.status_code == HTTP_OK, selection.text
            assert selection.json() == {
                "real_job_ids": [real_id],
                "total": review.json()["total"],
            }
            settings.hard_filters.company_blocklist = ["Acme"]
            filtered = await client.get(
                "/api/real-jobs", params={"decision": "results", "limit": 10000}
            )
            assert filtered.status_code == HTTP_OK, filtered.text
            assert filtered.json()["total"] == 0
            assert filtered.json()["jobs"] == []
            assert filtered.json()["tab_counts"]["results"] == 0
            hidden = await client.get(
                "/api/real-jobs/selection",
                params={"decision": "results"},
            )
            assert hidden.json() == {"real_job_ids": [], "total": 0}
            detail = await client.get(f"/api/real-jobs/{real_id}")
            assert detail.status_code == HTTP_OK
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_source_transition_resolves_parent(tmp_path: Path) -> None:
    path = tmp_path / "route.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        first = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="li-one",
                url="https://example.test/li-one",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        second = await store.save_job(
            JobPosting(
                platform="jobright",
                canonical_id="jr-two",
                url="https://example.test/jr-two",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build distributed systems and own API reliability. " * 20,
            )
        )
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE jobs SET real_job_id=(SELECT real_job_id FROM jobs WHERE id=?) "
                "WHERE id=?",
                (int(first.job_id), int(second.job_id)),
            )
            await db.commit()
            real_id = str(
                (
                    await (
                        await db.execute(
                            "SELECT real_job_id FROM jobs WHERE id=?",
                            (int(first.job_id),),
                        )
                    ).fetchone()
                )[0]
            )
        app = build_web_app(cast(AppContext, {"store": store, "settings": Settings()}))
        async with open_client(app) as client:
            response = await client.post(
                f"/api/jobs/{second.job_id}/transition",
                json={"to": "shortlisted"},
            )
            assert response.status_code == HTTP_OK, response.text
            assert response.json()["real_job_id"] == real_id
            collision = await client.post(
                f"/api/real-jobs/{second.job_id}/transition",
                json={"to": "ignored"},
            )
            assert collision.status_code == HTTP_OK, collision.text
            assert (
                await client.post(
                    f"/api/real-jobs/{real_id}/note",
                    json={"text": "reviewed"},
                )
            ).status_code == HTTP_OK
            assert (await store.get_real_job_status(real_id)).status == "shortlisted"
            assert (await store.get_real_job_status(second.job_id)).status == "ignored"
            assert (await store.get_status(first.job_id)).status == "new"
            assert (await store.get_status(second.job_id)).status == "new"
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "UPDATE jobs SET real_job_id=NULL WHERE id=?",
                    (int(second.job_id),),
                )
                await db.commit()
            unresolved = await client.post(
                f"/api/jobs/{second.job_id}/transition",
                json={"to": "ignored"},
            )
            assert unresolved.status_code == HTTP_CONFLICT, unresolved.text
            assert unresolved.json()["error"]["code"] == "identity_unresolved"
            assert (await store.get_status(second.job_id)).status == "new"
            library = await client.get("/api/jobs", params={"canonical": True})
            assert library.status_code == HTTP_OK, library.text
            assert library.json()["total"] == SOURCE_COUNT
            orphan = next(
                row for row in library.json()["jobs"] if row["id"] == second.job_id
            )
            assert orphan["real_job_id"] is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_apply_routes_record_one_canonical_event_with_source_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "apply-route.db"
    resume = tmp_path / "resume.md"
    resume.write_text("Engineer resume", encoding="utf-8")
    settings = Settings()
    settings.llm.master_resume_path = str(resume)
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="apply-route",
                url="https://example.test/posting",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        app = build_web_app(cast(AppContext, {"store": store, "settings": settings}))
        form = {
            "source_job_id": saved.job_id,
            "apply_url": "https://ats.example.test/submit",
        }
        async with open_client(app) as client:
            response = await client.post(f"/api/real-jobs/{real_id}/apply", data=form)
            assert response.status_code == HTTP_OK, response.text
            assert response.json()["applied"] is True
            repeat = await client.post(
                f"/api/jobs/{saved.job_id}/apply",
                data={"apply_url": form["apply_url"]},
            )
            assert repeat.status_code == HTTP_OK, repeat.text
            assert repeat.json()["applied"] is False
            events = await client.get("/api/real-jobs/applications")
            assert events.status_code == HTTP_OK, events.text
            assert events.json()["applications"] == [
                {
                    "id": events.json()["applications"][0]["id"],
                    "real_job_id": real_id,
                    "source_job_id": saved.job_id,
                    "source_applied_job_id": None,
                    "apply_url": form["apply_url"],
                    "applied_at": events.json()["applications"][0]["applied_at"],
                    "application_method": None,
                    "notes": None,
                }
            ]
            assert (await store.get_real_job_status(real_id)).status == "applied"
            assert (await store.get_status(saved.job_id)).status == "new"
            async with aiosqlite.connect(path) as db:
                row = await (
                    await db.execute(
                        "SELECT source_job_id,apply_url FROM real_job_applications"
                    )
                ).fetchone()
            assert row == (int(saved.job_id), form["apply_url"])
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_real_job_views_keep_parent_and_source_id_namespaces(
    tmp_path: Path,
) -> None:
    path = tmp_path / "views.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = []
        for platform, key in (
            ("linkedin", "one"),
            ("jobright", "two"),
            ("linkedin", "three"),
        ):
            saved.append(
                await store.save_job(
                    JobPosting(
                        platform=platform,
                        canonical_id=key,
                        url=f"https://example.test/{key}",
                        title=f"Engineer {key}",
                        company="Acme",
                        location="Remote",
                        discovered_at=datetime.now(UTC),
                        jd_text=f"Description for {key}",
                    )
                )
            )
        first_real = await store.resolve_real_job_id(saved[0].job_id)
        third_real = await store.resolve_real_job_id(saved[2].job_id)
        assert first_real is not None and third_real is not None
        policy = await current_policy_for_settings(
            Settings(), PersonalMLLearningService(store)
        )
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE jobs SET real_job_id=? WHERE id=?",
                (int(first_real), int(saved[1].job_id)),
            )
            await db.execute(
                "INSERT INTO real_job_evaluations("
                "real_job_id,source_job_id,input_jd_text,input_facts_json,"
                "stage_a_status,stage_a_score,stage_a_one_line,"
                "stage_b_status,stage_b_verdict,stage_b_json,updated_at) "
                "VALUES(?,?,?,?,'completed',90,'Strong match','completed',"
                "'apply',?,?)",
                (
                    int(first_real),
                    int(saved[0].job_id),
                    "Description for one",
                    json.dumps({
                        "stage_a_policy": policy.stage_a(),
                        "stage_b_policy": policy.stage_b(),
                    }),
                    json.dumps(
                        {
                            "verdict": "apply",
                            "jd_summary": "Role summary",
                            "fit_analysis": {
                                "score": STAGE_B_FIT_SCORE,
                                "strengths": [],
                                "gaps": [],
                            },
                            "raw_blocks": {},
                        }
                    ),
                    datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                ),
            )
            await db.execute(
                "INSERT INTO evaluations(job_id,stage_a_score,stage_a_one_line,"
                "stage_a_status) VALUES(?,?,?,'completed')",
                (int(saved[1].job_id), SOURCE_AUDIT_SCORE, "Historical source fit"),
            )
            await db.commit()
        await store.transition_real_job_status(
            TransitionRequest(job_id=third_real, new_status="ignored")
        )
        app = build_web_app(cast(AppContext, {"store": store, "settings": Settings()}))
        async with open_client(app) as client:
            page = await client.get(
                "/api/real-jobs",
                params={"decision": "results", "require_verdict": True, "limit": 1},
            )
            assert page.status_code == HTTP_OK, page.text
            assert page.json()["total"] == 1
            assert [row["id"] for row in page.json()["jobs"]] == [first_real]
            selection = await client.get(
                "/api/real-jobs/selection",
                params={"decision": "results", "require_verdict": True},
            )
            assert selection.status_code == HTTP_OK, selection.text
            assert selection.json() == {"real_job_ids": [first_real], "total": 1}
            assert saved[1].job_id not in selection.json()["real_job_ids"]
            assert page.json()["jobs"][0]["stage_b_fit_score"] == STAGE_B_FIT_SCORE
            assert page.json()["jobs"][0]["priority_score"] is not None
            detail = await client.get(f"/api/real-jobs/{first_real}")
            assert detail.status_code == HTTP_OK, detail.text
            assert {source["job_id"] for source in detail.json()["sources"]} == {
                saved[0].job_id,
                saved[1].job_id,
            }
            assert detail.json()["status"]["decision"] == "results"
            assert (
                detail.json()["evaluation"]["stage_b"]["fit_score"] == STAGE_B_FIT_SCORE
            )
            source_detail = await client.get(f"/api/jobs/{saved[1].job_id}")
            assert source_detail.status_code == HTTP_OK, source_detail.text
            assert source_detail.json()["real_job_id"] == first_real
            assert source_detail.json()["job"]["jd_text"] == "Description for two"
            assert source_detail.json()["status"]["decision"] == "results"
            assert (
                source_detail.json()["evaluation"]["stage_b"]["fit_score"]
                == STAGE_B_FIT_SCORE
            )
            source_audit = await client.get(f"/api/jobs/{saved[1].job_id}/audit")
            assert source_audit.status_code == HTTP_OK, source_audit.text
            assert source_audit.json()["job"]["id"] == saved[1].job_id
            assert source_audit.json()["job"]["jd_text"] == "Description for two"
            assert (
                source_audit.json()["evaluation"]["stage_a"]["score"]
                == SOURCE_AUDIT_SCORE
            )
            assert source_audit.json()["evaluation"]["stage_b"] is None
            colliding_real = await client.get(f"/api/real-jobs/{saved[1].job_id}")
            assert colliding_real.status_code == HTTP_NOT_FOUND
            library = await client.get(
                "/api/jobs", params={"canonical": True, "decision": "results"}
            )
            assert library.status_code == HTTP_OK, library.text
            assert library.json()["total"] == SOURCE_COUNT
            assert {row["id"] for row in library.json()["jobs"]} == {
                saved[0].job_id,
                saved[1].job_id,
            }
            assert {row["real_job_id"] for row in library.json()["jobs"]} == {
                first_real
            }
    finally:
        await store.close()
