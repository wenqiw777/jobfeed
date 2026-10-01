"""AI-data contributors never reach paid scoring or the Results action set."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import QualityBand
from jobfeed.services._evaluate_canonical import _policy_for_run
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import EvaluateDependencies
from tests.integration.test_canonical_evaluate_service import _PaidFake
from tests.support.factories import make_job
from tests.unit.test_evaluate_lease_scheduling import _LeaseProbe, _service

_EXISTING_A_SCORE = 92


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("stage", ["both", "b"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "role",
    [
        ("Machine Learning Engineer - AI Trainer", "Example"),
        ("Software Developer", "DataAnnotation"),
        ("Data Annotator", "Example"),
    ],
)
async def test_trainer_never_calls_paid_scoring(
    tmp_path: Path,
    canonical: bool,
    stage: str,
    dry_run: bool,
    role: tuple[str, str],
) -> None:
    title, company = role
    store = SQLiteStore(tmp_path / "trainer.db")
    await store.connect()
    try:
        saved = await store.save_job(
            make_job(
                title=title,
                company=company,
                discovered_at=datetime.now(UTC),
                jd_text="Write coding problems and evaluate AI responses. " * 15,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert real_id is not None
        template, _, _ = _service(_LeaseProbe())
        llm = _PaidFake()
        service = EvaluateService(
            deps=EvaluateDependencies(
                store=store,
                store_ops=store,
                store_status=store,
                prompt_renderer=JinjaPromptRenderer(Path("src/jobfeed/templates")),
                llm_stage_a=llm,
                llm_stage_b=llm,
            ),
            config=template._config,
            logger=template._logger,
        )
        if stage == "b":
            if canonical:
                policy = await _policy_for_run(service, dry_run=False)
                await store.claim_real_job_stage_a_by_ids(
                    [real_id],
                    stage_a_policy=policy.stage_a(),
                    stage_b_policy=policy.stage_b(),
                )
                async with store._lifecycle.connection() as db:
                    await db.execute(
                        "UPDATE real_job_evaluations SET stage_a_status='completed',"
                        "stage_a_score=92,stage_a_model='mock-a' WHERE real_job_id=?",
                        (int(real_id),),
                    )
            else:
                async with store._lifecycle.connection() as db:
                    await db.execute(
                        "INSERT INTO evaluations(job_id,stage_a_status,stage_a_score) "
                        "VALUES(?,'completed',92)",
                        (int(saved.job_id),),
                    )
        run = await service.run(
            stage=stage,
            limit=10,
            job_ids=[saved.job_id],
            canonical=canonical,
            dry_run=dry_run,
        )
        assert llm.calls == 0
        assert run.stage_a_scored == run.stage_b_scored == 0
        assert run.dry_run_preview == []
        assert run.jobs_filtered == 1
        if stage == "b":
            async with store._lifecycle.connection() as db:
                cursor = await db.execute(
                    "SELECT stage_a_score,stage_b_status FROM "
                    + ("real_job_evaluations" if canonical else "evaluations")
                )
                score, status = await cursor.fetchone()
                assert score == _EXISTING_A_SCORE
                assert status != "in_progress"
    finally:
        await store.close()


async def test_results_exclusion_keeps_library_and_evaluation_history(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "results.db")
    await store.connect()
    try:
        ids = []
        for key, title, company in (
            ("trainer", "Software Engineer - AI Trainer", "Example"),
            ("hidden-trainer", "Software Developer", "DataAnnotation"),
            ("engineer", "Machine Learning Engineer", "Example"),
        ):
            saved = await store.save_job(
                make_job(
                    canonical_id=key,
                    title=title,
                    company=company,
                    jd_text="Build production ML services and annotation tools. " * 12,
                    jd_quality=QualityBand.FULL,
                )
            )
            ids.append(await store.resolve_real_job_id(saved.job_id))
        async with store._lifecycle.connection() as db:
            await db.execute(
                "INSERT INTO real_job_evaluations(real_job_id,source_job_id,"
                "input_jd_text,input_facts_json,updated_at,"
                "stage_a_status,stage_a_score,stage_b_status,stage_b_verdict) "
                "SELECT real_job_id,id,jd_text,'{}',discovered_at,"
                "'completed',92,'completed','apply' FROM jobs"
            )
        page = await store.query_real_jobs_view(decision="results", limit=1)
        assert page["total"] == page["tab_counts"]["results"] == 1
        assert page["jobs"][0]["real_job_id"] == int(ids[-1])
        selected = await store.select_real_job_ids(decision="results")
        assert selected["real_job_ids"] == [ids[-1]]
        assert (
            await store.query_real_jobs_view(
                decision="results", search="DataAnnotation"
            )
        )["total"] == 0
        library = await store.query_source_library(
            decision=None, sort="discovered_desc", search=None, limit=10, offset=0
        )
        assert library["total"] == len(ids)
        async with store._lifecycle.connection() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM real_job_evaluations "
                "WHERE stage_b_verdict='apply'"
            )
            assert (await cursor.fetchone())[0] == len(ids)
    finally:
        await store.close()
