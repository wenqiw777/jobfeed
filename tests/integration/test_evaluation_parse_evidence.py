"""Every malformed paid response survives retries and process restart."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import JobPosting, LLMRequest, LLMResponse, QualityBand
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import EvaluateDependencies
from tests.integration.test_canonical_evaluate_service import _PaidFake
from tests.unit.test_evaluate_lease_scheduling import _LeaseProbe, _service


@pytest.mark.parametrize("canonical", [True, False])
@pytest.mark.parametrize("stage", ["a", "b"])
@pytest.mark.parametrize("terminal", [True, False])
async def test_parse_attempts_survive_restart(tmp_path, canonical, stage, terminal):
    path = tmp_path / "evidence.sqlite"
    store = SQLiteStore(path)
    await store.connect()
    raw = '这是完整保留的原文\n{"broken":' + "x" * 5000

    class Responses(_PaidFake):
        failed_calls = 0

        async def complete(self, request: LLMRequest) -> LLMResponse:
            response = await super().complete(request)
            if request.model == "mock-" + stage:
                self.failed_calls += 1
                if terminal or self.failed_calls == 1:
                    response.content = raw + str(self.failed_calls)
            return response

    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="evidence",
                url="https://example.test/job",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build software services. " * 30,
                jd_quality=QualityBand.FULL,
            )
        )
        template, _, _ = _service(_LeaseProbe())
        llm = Responses()
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
        run = await service.run(
            stage="both", limit=1, job_ids=[saved.job_id], canonical=canonical
        )
    finally:
        await store.close()
    async with aiosqlite.connect(path) as db:
        rows = await (
            await db.execute(
                "SELECT key,value FROM state WHERE key LIKE 'evaluation-parse-error:%'"
            )
        ).fetchall()
    expected_attempts = [1, 2] if terminal else [1]
    records = sorted((json.loads(r[1]) for r in rows), key=lambda r: r["attempt"])
    assert [r["attempt"] for r in records] == expected_attempts
    assert len({r[0] for r in rows}) == len(rows)
    for record in records:
        assert record["raw_response"] == raw + str(record["attempt"])
        assert record["run_id"] == run.run_id
        assert record["source_job_id"] == saved.job_id
        assert record["stage"] == stage
        assert record["model"] == "mock-" + stage
        assert record["error"]
        assert record["created_at"]
        assert record["cost_usd"] == pytest.approx(0.1)
        assert bool(record["real_job_id"]) == canonical
    assert run.errors == int(terminal)
