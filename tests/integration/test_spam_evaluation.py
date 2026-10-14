"""Spam evidence prevents paid calls in every evaluation mode."""

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
async def test_spam_never_calls_paid_scoring(
    tmp_path: Path,
    canonical: bool,
    stage: str,
    dry_run: bool,
) -> None:
    store = SQLiteStore(tmp_path / "trainer.db")
    await store.connect()
    try:
        saved = await store.save_job(
            make_job(
                title="Software Engineer",
                company="New Publisher",
                discovered_at=datetime.now(UTC),
                jd_text=(
                    "Help train and evaluate AI models. This is a project-based role. "
                )
                * 15,
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
                await store.spam_decide(saved.job_id, scope="job", decision="allow")
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
        await store.spam_decide(saved.job_id, scope="job", decision="reset")
        run = await service.run(
            stage=stage,
            limit=10,
            job_ids=[saved.job_id],
            canonical=canonical,
            dry_run=dry_run,
        )
        async with store._lifecycle.connection() as db:
            row = await (
                await db.execute(
                    "SELECT hard_filter FROM jobs WHERE id=?", (int(saved.job_id),)
                )
            ).fetchone()
            assert not (row[0] or "").startswith("spam:")
        assert llm.calls == 0
        assert run.stage_a_scored == run.stage_b_scored == 0
        assert run.dry_run_preview == []
        if stage == "both":
            await store.spam_decide(saved.job_id, scope="job", decision="allow")
            restored = await service.run(
                stage=stage,
                limit=10,
                job_ids=[saved.job_id],
                canonical=canonical,
                dry_run=True,
            )
            assert len(restored.dry_run_preview) == 1
            assert llm.calls == 0
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
