"""The paid service consumes real jobs after source-scope resolution."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import aiosqlite
import pytest

import jobfeed.adapters.store._sqlite_real_job_evaluation as sqlite_canonical
import jobfeed.services._evaluate_canonical as canonical_evaluate
from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.config import Settings
from jobfeed.domain.models import (
    JobPosting,
    LLMRequest,
    LLMResponse,
    MLGateResult,
    QualityBand,
)
from jobfeed.domain.real_job_evaluation import RealJobEvaluationInput
from jobfeed.domain.seniority import SeniorityDecision
from jobfeed.evaluation_config import (
    current_policy_for_settings,
    evaluation_runtime_config,
)
from jobfeed.personal_ml_learning import PersonalMLLearningService
from jobfeed.services._evaluate_canonical import (
    _policy_for_run,
    _release_stage_a_claims,
    _release_stage_b_claims,
)
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import EvaluateDependencies
from tests.unit.test_evaluate_lease_scheduling import _LeaseProbe, _service
from tests.unit.test_scoring import make_stage_b_payload


class _PaidFake:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=(
                json.dumps(make_stage_b_payload())
                if request.model == "mock-b"
                else '{"score": 90, "one_line": "Fit", "timing_eligible": "eligible"}'
            ),
            model=request.model,
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.1,
        )


EXPECTED_PAID_CALLS = 2
EXPECTED_QUICK_SCORE = 90
EXPECTED_PIPELINE_CALLS = 4
CLEANUP_CONCURRENCY = 2


async def test_canonical_claim_cleanup_respects_concurrency_limit() -> None:
    active = 0
    max_active = 0

    class CleanupStore:
        async def _release(self) -> bool:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
            finally:
                active -= 1
            return True

        async def release_real_job_stage_a_claim(
            self,
            _real_job_id: str,
            *,
            expected_revision: int,
            expected_generation: int,
        ) -> bool:
            del expected_revision, expected_generation
            return await self._release()

        async def release_real_job_stage_b_claim(
            self,
            _real_job_id: str,
            *,
            expected_revision: int,
            expected_generation: int,
        ) -> bool:
            del expected_revision, expected_generation
            return await self._release()

    claims = [
        RealJobEvaluationInput(
            real_job_id=str(index),
            source_job_id=str(index),
            job=JobPosting(
                id=str(index),
                platform="linkedin",
                canonical_id=f"cleanup-{index}",
                url=f"https://example.test/cleanup/{index}",
                title="Software Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            ),
        )
        for index in range(20)
    ]

    await _release_stage_a_claims(
        CleanupStore(),
        claims,
        max_concurrent=CLEANUP_CONCURRENCY,
    )

    assert max_active == CLEANUP_CONCURRENCY

    max_active = 0
    await _release_stage_b_claims(
        CleanupStore(),
        claims,
        max_concurrent=CLEANUP_CONCURRENCY,
    )

    assert max_active == CLEANUP_CONCURRENCY


async def test_stage_b_only_run_keeps_configured_stage_a_gate_policy(
    tmp_path: Path,
) -> None:
    """A B-only runner lacks an ML adapter but still reads A's saved policy."""
    store = SQLiteStore(tmp_path / "stage-b-policy.db")
    await store.connect()
    try:
        settings = Settings()
        settings.scoring.ml_gate_enabled = True
        personal_ml = PersonalMLLearningService(store)
        expected = await current_policy_for_settings(settings, personal_ml)
        runner = SimpleNamespace(
            _deps=SimpleNamespace(ml_gate=None, personal_ml=personal_ml),
            _config=evaluation_runtime_config(settings),
        )
        actual = await _policy_for_run(cast(EvaluateService, runner), dry_run=False)
        assert actual.stage_a() == expected.stage_a()
    finally:
        await store.close()


async def test_canonical_runner_discovers_candidates_before_concurrent_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(canonical_evaluate, "_CANONICAL_BATCH_SIZE", 1)
    path = tmp_path / "queued.db"
    monkeypatch.setattr(
        canonical_evaluate,
        "_ML_GATE_PROGRESS_BATCH_SIZE",
        1,
        raising=False,
    )
    store = SQLiteStore(path)
    await store.connect()
    try:
        ids = []
        for key in ("one", "two"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title=f"Engineer {key}",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and APIs. " * 10,
                    jd_quality=QualityBand.FULL,
                )
            )
            ids.append(saved.job_id)

        class ObservingLLM(_PaidFake):
            def __init__(self) -> None:
                super().__init__()
                self.active: dict[str, int] = {}
                self.max_active: dict[str, int] = {}
                self.all_started: dict[str, asyncio.Event] = {}

            async def complete(self, request: LLMRequest) -> LLMResponse:
                stage = "stage_b" if request.model == "mock-b" else "stage_a"
                async with aiosqlite.connect(path) as db:
                    count = (
                        await (
                            await db.execute(
                                "SELECT COUNT(*) FROM real_job_evaluations "
                                f"WHERE {stage}_status='in_progress'",
                            )
                        ).fetchone()
                    )[0]
                assert count == EXPECTED_PAID_CALLS
                self.active[stage] = self.active.get(stage, 0) + 1
                self.max_active[stage] = max(
                    self.max_active.get(stage, 0), self.active[stage]
                )
                all_started = self.all_started.setdefault(stage, asyncio.Event())
                if self.active[stage] == EXPECTED_PAID_CALLS:
                    all_started.set()
                try:
                    await asyncio.wait_for(all_started.wait(), timeout=1)
                    return await super().complete(request)
                finally:
                    self.active[stage] -= 1

        template, _, _ = _service(_LeaseProbe())
        llm = ObservingLLM()
        pipeline_order: list[str] = []

        class PassGate:
            async def predict_batch(self, jobs):
                assert len(jobs) == 1
                assert "seniority" not in pipeline_order
                pipeline_order.append("sde")
                return [MLGateResult(score=0.95, result="pass") for _ in jobs]

        class PassSeniority:
            async def predict_batch(self, jobs):
                assert len(jobs) == EXPECTED_PAID_CALLS
                assert pipeline_order == ["sde", "sde"]
                pipeline_order.append("seniority")
                return [
                    SeniorityDecision(
                        result="in_scope",
                        reason="test",
                        yoe_min=2,
                        confidence=0.99,
                    )
                    for _ in jobs
                ]

        progress: list[tuple[str | None, int | None, int]] = []
        service = EvaluateService(
            deps=EvaluateDependencies(
                store=store,
                store_ops=store,
                store_status=store,
                prompt_renderer=JinjaPromptRenderer(Path("src/jobfeed/templates")),
                llm_stage_a=llm,
                llm_stage_b=llm,
                ml_gate=PassGate(),
                seniority_gate=PassSeniority(),
            ),
            config=replace(
                template._config,
                llm=replace(
                    template._config.llm,
                    max_concurrent=EXPECTED_PAID_CALLS,
                ),
                ml_gate_enabled=True,
                seniority_gate_mode="filter",
            ),
            logger=template._logger,
        )
        await service.run(
            stage="both",
            limit=2,
            job_ids=ids,
            canonical=True,
            on_progress=lambda run: progress.append(
                (run.progress_stage, run.stage_a_total, run.ml_gate_processed)
            ),
        )
        assert llm.calls == EXPECTED_PIPELINE_CALLS
        assert pipeline_order == ["sde", "sde", "seniority"]
        assert llm.max_active == {
            "stage_a": EXPECTED_PAID_CALLS,
            "stage_b": EXPECTED_PAID_CALLS,
        }
        assert ("preparing", EXPECTED_PAID_CALLS, 0) in progress
        ml_gate_progress = [
            (total, processed)
            for stage, total, processed in progress
            if stage == "ml_gate"
        ]
        assert ml_gate_progress
        assert ml_gate_progress[0][0] == EXPECTED_PAID_CALLS
        assert (EXPECTED_PAID_CALLS, 1) in ml_gate_progress
    finally:
        await store.close()


async def test_canonical_runner_reaches_claimable_job_after_twenty_pages(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "deep-backlog.db")
    await store.connect()
    try:
        now = datetime.now(UTC)
        valid = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="deep-valid",
                url="https://example.test/deep-valid",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=now,
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        for index in range(20):
            await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=f"deep-unclaimable-{index}",
                    url=f"https://example.test/deep-unclaimable-{index}",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=now,
                    jd_text="",
                    jd_quality=QualityBand.PARTIAL,
                )
            )
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
            config=replace(template._config, ml_gate_max_candidates=1),
            logger=template._logger,
        )
        await service.run(stage="a", limit=1, canonical=True)
        assert llm.calls == 1
        real_id = await store.resolve_real_job_id(valid.job_id)
        detail = await store.get_real_job_view(real_id)
        assert detail is not None
        assert detail["row"]["stage_a_score"] == EXPECTED_QUICK_SCORE
    finally:
        await store.close()


async def test_canonical_runner_preserves_scores_when_model_or_gate_policy_changes(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "policy-service.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="policy-service",
                url="https://example.test/policy-service",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )

        class PassGate:
            async def predict_batch(self, jobs):
                return [MLGateResult(score=0.95, result="pass") for _ in jobs]

        template, _, _ = _service(_LeaseProbe())
        llm = _PaidFake()
        config = replace(
            template._config,
            ml_gate_enabled=True,
            ml_gate_model_version="gate-v1",
        )
        service = EvaluateService(
            deps=EvaluateDependencies(
                store=store,
                store_ops=store,
                store_status=store,
                prompt_renderer=JinjaPromptRenderer(Path("src/jobfeed/templates")),
                llm_stage_a=llm,
                llm_stage_b=llm,
                ml_gate=PassGate(),
            ),
            config=config,
            logger=template._logger,
        )
        await service.run(stage="a", limit=1, job_ids=[saved.job_id], canonical=True)
        await service.run(stage="a", limit=1, job_ids=[saved.job_id], canonical=True)
        assert llm.calls == 1
        service._config = replace(config, llm=replace(config.llm, stage_a="mock-a-v2"))
        stale_preview = await service.run(
            stage="a",
            limit=1,
            job_ids=[saved.job_id],
            canonical=True,
            dry_run=True,
        )
        assert stale_preview.dry_run_preview == []
        await service.run(stage="a", limit=1, job_ids=[saved.job_id], canonical=True)
        expected_after_model_change = 1
        assert llm.calls == expected_after_model_change
        service._config = replace(service._config, ml_gate_model_version="gate-v2")
        await service.run(stage="a", limit=1, job_ids=[saved.job_id], canonical=True)
        expected_after_gate_change = 1
        assert llm.calls == expected_after_gate_change
        real_id = await store.resolve_real_job_id(saved.job_id)
        async with aiosqlite.connect(tmp_path / "policy-service.db") as db:
            reasons = await (
                await db.execute(
                    "SELECT reason FROM real_job_evaluation_history "
                    "WHERE real_job_id=? ORDER BY id",
                    (int(real_id),),
                )
            ).fetchall()
        assert reasons == []
    finally:
        await store.close()


async def test_canonical_failed_corpus_only_retries_errors(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "failed.db")
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="failed-corpus",
                url="https://example.test/failed-corpus",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
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
        await service.run(
            stage="a",
            corpus="failed",
            limit=1,
            job_ids=[saved.job_id],
            canonical=True,
        )
        assert llm.calls == 0
        real_id = await store.resolve_real_job_id(saved.job_id)
        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        await store.save_real_job_stage_a_error(
            real_id,
            "transient",
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        await service.run(
            stage="a",
            corpus="failed",
            limit=1,
            job_ids=[saved.job_id],
            canonical=True,
        )
        assert llm.calls == 1
    finally:
        await store.close()


async def test_canonical_backlog_pages_past_repost(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "backlog.db")
    await store.connect()
    try:
        for key, repost in (("eligible", False), ("repost", True)):
            await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title=f"Engineer {key}",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and APIs. " * 10,
                    jd_quality=QualityBand.FULL,
                    is_repost=repost,
                )
            )
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
            config=replace(template._config, ml_gate_max_candidates=1),
            logger=template._logger,
        )
        await service.run(stage="a", limit=1, canonical=True)
        assert llm.calls == 1
    finally:
        await store.close()


@pytest.mark.parametrize("stage,expected_calls", [("a", 1), ("both", 2)])
async def test_canonical_service_scores_two_aliases_once(
    tmp_path: Path, stage: str, expected_calls: int
) -> None:
    store = SQLiteStore(tmp_path / "service.db")
    await store.connect()
    try:
        ids = []
        for platform, key, url in (
            ("linkedin", "123", "https://www.linkedin.com/jobs/view/123/"),
            (
                "jobright",
                "other",
                "https://careers.southwestair.com/us/en/job/REQ123/engineer",
            ),
        ):
            saved = await store.save_job(
                JobPosting(
                    platform=platform,
                    canonical_id=key,
                    url=url,
                    title="Software Engineer",
                    company="Southwest Airlines",
                    location="Dallas, TX",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and APIs. " * 10,
                    jd_quality=QualityBand.FULL,
                )
            )
            ids.append(saved.job_id)
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
        preview = await service.run(
            stage="a", limit=10, job_ids=ids, canonical=True, dry_run=True
        )
        assert len(preview.dry_run_preview) == 1
        assert (
            preview.dry_run_preview[0].job_id
            == (await store.resolve_real_job_ids(ids))[0]
        )
        assert llm.calls == 0
        await service.run(stage=stage, limit=10, job_ids=ids, canonical=True)
        assert llm.calls == expected_calls
        assert (
            await store.claim_real_job_stage_a_by_ids(
                await store.resolve_real_job_ids(ids)
            )
            == []
        )
    finally:
        await store.close()


@pytest.mark.parametrize("stage", ["a", "b"])
async def test_backlog_finishes_preparation_before_any_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """All candidate pages must finish before any paid scoring starts."""
    batch_size = 2
    total = 3
    monkeypatch.setattr(canonical_evaluate, "_CANONICAL_BATCH_SIZE", batch_size)
    store = SQLiteStore(tmp_path / "stream.db")
    await store.connect()
    try:
        for index in range(3):
            await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=f"stream-{index}",
                    url=f"https://example.test/stream-{index}",
                    title="Engineer",
                    company=f"Company {index}",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and APIs. " * 10,
                    jd_quality=QualityBand.FULL,
                )
            )
        llm = _PaidFake()
        original = store.list_real_job_ids_for_evaluation
        pages = []

        async def observe(**kwargs):
            assert kwargs["limit"] <= batch_size
            assert llm.calls == 0, "scoring started before candidate preparation ended"
            page = await original(**kwargs)
            pages.append(page)
            return page

        template, _, _ = _service(_LeaseProbe())
        service = EvaluateService(
            deps=EvaluateDependencies(
                store=store,
                store_ops=store,
                store_status=store,
                prompt_renderer=JinjaPromptRenderer(Path("src/jobfeed/templates")),
                llm_stage_a=llm,
                llm_stage_b=llm,
            ),
            config=replace(template._config, ml_gate_max_candidates=1_000_000),
            logger=template._logger,
        )
        if stage == "b":
            await service.run(stage="a", limit=total, canonical=True)
            llm.calls = 0
        monkeypatch.setattr(store, "list_real_job_ids_for_evaluation", observe)
        progress = []
        run = await service.run(
            stage=stage,
            limit=total,
            canonical=True,
            on_progress=lambda run: progress.append(
                (run.progress_stage, run.stage_a_processed)
            ),
        )
        assert llm.calls == total
        assert getattr(run, f"stage_{stage}_processed") == total
        assert getattr(run, f"stage_{stage}_total") == total
        assert len(pages) == batch_size
        if stage == "a":
            assert all(
                phase in {"stage_a", "finalizing"} for phase, done in progress if done
            )
    finally:
        await store.close()


async def test_canonical_claim_preserves_new_first_and_reuses_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "claim-order.db")
    await store.connect()
    try:
        ids = []
        for index in range(3):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=f"claim-{index}",
                    url=f"https://example.test/claim-{index}",
                    title="Engineer",
                    company=f"Company {index}",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and APIs. " * 10,
                    jd_quality=QualityBand.FULL,
                )
            )
            ids.append(await store.resolve_real_job_id(saved.job_id))
        source_queries = []
        original_all = sqlite_canonical._all

        async def track_source_queries(connection, sql, params):
            if sql.startswith("SELECT * FROM jobs"):
                source_queries.append(sql)
            return await original_all(connection, sql, params)

        monkeypatch.setattr(sqlite_canonical, "_all", track_source_queries)
        calls = 0
        original = store._lifecycle.connection

        def count_connections():
            nonlocal calls
            calls += 1
            return original()

        monkeypatch.setattr(store._lifecycle, "connection", count_connections)
        claims = await store.claim_real_job_stage_a_by_ids(list(reversed(ids)))
        assert [claim.real_job_id for claim in claims] == list(reversed(ids))
        assert calls == 1
        assert len(source_queries) == 1
        assert await store.claim_real_job_stage_a_by_ids(ids) == []
    finally:
        await store.close()


@pytest.mark.parametrize("stage", ["a", "b"])
async def test_cancelled_stream_releases_all_claims_before_store_close(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "cancel-stream.db"
    store = SQLiteStore(path)
    await store.connect()
    saved = await store.save_job(
        JobPosting(
            platform="linkedin",
            canonical_id="cancel-stream",
            url="https://example.test/cancel-stream",
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=datetime.now(UTC),
            jd_text="Build production software services and APIs. " * 10,
            jd_quality=QualityBand.FULL,
        )
    )
    template, _, _ = _service(_LeaseProbe())

    class CancelLLM(_PaidFake):
        cancel = False

        async def complete(self, request):
            if self.cancel:
                raise asyncio.CancelledError
            return await super().complete(request)

    llm = CancelLLM()
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
        await service.run(stage="a", limit=1, job_ids=[saved.job_id], canonical=True)
    llm.cancel = True
    with pytest.raises(asyncio.CancelledError):
        await service.run(stage=stage, limit=1, job_ids=[saved.job_id], canonical=True)
    await store.close()
    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM real_job_evaluations "
                f"WHERE stage_{stage}_status='in_progress'"
            )
        ).fetchone()
        assert row == (0,)
