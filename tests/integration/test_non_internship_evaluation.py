"""Explicit non-internship requirements stop both paid evaluation stages."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import QualityBand
from jobfeed.domain.seniority import SeniorityInput
from jobfeed.services._evaluate_canonical import _policy_for_run
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import EvaluateDependencies
from jobfeed.services.seniority_gate import HybridSeniorityGate
from tests.integration.test_canonical_evaluate_service import _PaidFake
from tests.support.factories import make_job
from tests.unit.test_evaluate_lease_scheduling import _LeaseProbe, _service


class _NoModelCalls:
    async def predict_out_of_scope(self, _jobs: list[SeniorityInput]) -> list[float]:
        pytest.fail("explicit rule must bypass model scoring")


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("stage", ["both", "b"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("mode", ["filter", "off", "shadow"])
async def test_non_internship_requirement_respects_modes_and_paid_boundaries(
    tmp_path: Path, canonical: bool, stage: str, dry_run: bool, mode: str
) -> None:
    store = SQLiteStore(tmp_path / "non-internship.db")
    await store.connect()
    try:
        saved = await store.save_job(
            make_job(
                title="Software Development Engineer II",
                discovered_at=datetime.now(UTC),
                jd_text="Basic Qualifications: 3+ years of non-internship "
                "professional software development experience. "
                + "Build production software services and APIs. "
                * 12,
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
                seniority_gate=HybridSeniorityGate(
                    model=_NoModelCalls(), out_of_scope_threshold=0.9
                ),
            ),
            config=replace(template._config, seniority_gate_mode=mode),
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
        if mode == "filter":
            assert llm.calls == run.stage_a_scored == run.stage_b_scored == 0
            assert run.dry_run_preview == []
            assert run.jobs_seniority_filtered == 1
            if stage == "b":
                async with store._lifecycle.connection() as db:
                    cursor = await db.execute(
                        "SELECT stage_a_score,stage_b_status FROM "
                        + ("real_job_evaluations" if canonical else "evaluations")
                    )
                    score, status = await cursor.fetchone()
                    expected_score = 92
                    assert score == expected_score
                    assert status != "in_progress"
        else:
            assert run.jobs_seniority_filtered == 0
            if dry_run:
                assert run.dry_run_preview
                assert llm.calls == 0
            else:
                expected_calls = 2 if stage == "both" else 1
                assert llm.calls == expected_calls
    finally:
        await store.close()
