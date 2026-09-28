"""Real temporary SQLite receipts for exact reuse with a synthetic LLM."""

from dataclasses import replace
from pathlib import Path

from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.domain.models import LLMRequest, LLMResponse, QualityBand
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import EvaluateDependencies
from tests.unit.test_evaluate_lease_scheduling import _job, _LeaseProbe, _service


class PaidFake:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=(
                '{"score": 80, "one_line": "Synthetic Python match", '
                '"timing_eligible": "eligible"}'
            ),
            model=request.model,
            input_tokens=10,
            output_tokens=10,
            cost_usd=0.125,
        )


async def test_exact_requests_save_each_post_and_never_reuse_across_runs(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "evaluation.sqlite")
    await store.connect()
    try:
        for index in range(2):
            await store.save_job(
                replace(
                    _job(),
                    id=None,
                    canonical_id=f"post-{index}",
                    url=f"https://example.test/{index}",
                    jd_quality=QualityBand.FULL,
                )
            )
        template, _, _ = _service(_LeaseProbe())
        llm = PaidFake()
        deps = EvaluateDependencies(
            store=store,
            store_ops=store,
            store_status=store,
            prompt_renderer=JinjaPromptRenderer(Path("src/jobfeed/templates")),
            llm_stage_a=llm,
            llm_stage_b=llm,
        )
        service = EvaluateService(
            deps=deps, config=template._config, logger=template._logger
        )
        await service.run(stage="a", limit=10)
        assert llm.calls == 1
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT stage_a_status, stage_a_cost_usd FROM evaluations "
                "ORDER BY stage_a_cost_usd"
            )
            assert [tuple(row) for row in await cursor.fetchall()] == [
                ("completed", 0.0),
                ("completed", 0.125),
            ]
            await cursor.close()
            cursor = await connection.execute(
                "SELECT SUM(calls),SUM(spent_usd) FROM cost_ledger"
            )
            assert tuple(await cursor.fetchone()) == (1, 0.125)
            await cursor.close()
        assert service._reuse.retained_bytes == 0
        await store.save_job(
            replace(
                _job(),
                id=None,
                canonical_id="post-later",
                url="https://example.test/later",
                jd_quality=QualityBand.FULL,
            )
        )
        await service.run(stage="a", limit=10)
        assert llm.calls == 2  # noqa: PLR2004 -- one paid call in each independent run
        async with store._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM evaluations WHERE stage_a_status='completed'"
            )
            assert (await cursor.fetchone())[0] == 3  # noqa: PLR2004
            await cursor.close()
    finally:
        await store.close()
