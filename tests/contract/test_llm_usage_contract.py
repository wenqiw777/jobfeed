"""Contract tests for the record_llm_usage store method (PostgreSQL backend).

Tests exercise the ``record_llm_usage`` Protocol method and verify
persistence via raw SQL reads against the ``llm_usage`` table.
"""

import asyncpg
import pytest

from jobfeed.domain.models_llm import LLMUsage
from tests.support.factories import FIXED_TIME, make_job

pytestmark = pytest.mark.postgres

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_MODEL = "claude-sonnet-4-20250514"
INPUT_TOKENS = 1200
OUTPUT_TOKENS = 350
COST_USD = 0.0042
LATENCY_MS = 890
FLOAT_TOLERANCE = 1e-6
ROLLBACK_COST_DAY = "2099-12-30"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecordLLMUsage:
    """Contract tests for record_llm_usage with and without job_id FK."""

    async def test_record_with_job_id(self, contract_store):
        """Record LLMUsage with job_id set, query back, all fields match."""
        # Create a job so the FK is valid
        job = make_job("llm-usage-1")
        result = await contract_store.save_job(job)
        job_id = result.job_id

        run_id = "run-llm-usage-1"
        usage = LLMUsage(
            model=TEST_MODEL,
            input_tokens=INPUT_TOKENS,
            output_tokens=OUTPUT_TOKENS,
            cost_usd=COST_USD,
            cached=True,
            latency_ms=LATENCY_MS,
            timestamp=FIXED_TIME,
            job_id=job_id,
            stage="a",
            run_id=run_id,
        )
        await contract_store.record_llm_usage(usage)

        # Read back via raw SQL
        pool = contract_store._get_pool()
        row = await pool.fetchrow("SELECT * FROM llm_usage WHERE run_id = $1", run_id)
        assert row is not None
        assert row["model"] == TEST_MODEL
        assert row["input_tokens"] == INPUT_TOKENS
        assert row["output_tokens"] == OUTPUT_TOKENS
        assert abs(float(row["cost_usd"]) - COST_USD) < FLOAT_TOLERANCE
        assert row["cached"] is True
        assert row["latency_ms"] == LATENCY_MS
        assert row["job_id"] == int(job_id)
        assert row["stage"] == "a"
        assert row["run_id"] == run_id

    async def test_record_without_job_id(self, contract_store):
        """Record LLMUsage with job_id=None inserts with NULL FK."""
        run_id = "run-llm-usage-none"
        usage = LLMUsage(
            model=TEST_MODEL,
            input_tokens=INPUT_TOKENS,
            output_tokens=OUTPUT_TOKENS,
            cost_usd=COST_USD,
            cached=False,
            latency_ms=LATENCY_MS,
            timestamp=FIXED_TIME,
            job_id=None,
            stage=None,
            run_id=run_id,
        )
        await contract_store.record_llm_usage(usage)

        pool = contract_store._get_pool()
        row = await pool.fetchrow("SELECT * FROM llm_usage WHERE run_id = $1", run_id)
        assert row is not None
        assert row["model"] == TEST_MODEL
        assert row["input_tokens"] == INPUT_TOKENS
        assert row["output_tokens"] == OUTPUT_TOKENS
        assert row["cached"] is False
        assert row["job_id"] is None
        assert row["stage"] is None
        assert row["run_id"] == run_id

    @pytest.mark.parametrize(
        "usage",
        [
            LLMUsage(
                model=TEST_MODEL,
                input_tokens=-1,
                output_tokens=OUTPUT_TOKENS,
                cost_usd=COST_USD,
                cached=False,
                latency_ms=LATENCY_MS,
                timestamp=FIXED_TIME,
                stage="a",
            ),
            LLMUsage(
                model=TEST_MODEL,
                input_tokens=INPUT_TOKENS,
                output_tokens=-1,
                cost_usd=COST_USD,
                cached=False,
                latency_ms=LATENCY_MS,
                timestamp=FIXED_TIME,
                stage="a",
            ),
            LLMUsage(
                model=TEST_MODEL,
                input_tokens=INPUT_TOKENS,
                output_tokens=OUTPUT_TOKENS,
                cost_usd=-0.01,
                cached=False,
                latency_ms=LATENCY_MS,
                timestamp=FIXED_TIME,
                stage="a",
            ),
            LLMUsage(
                model=TEST_MODEL,
                input_tokens=INPUT_TOKENS,
                output_tokens=OUTPUT_TOKENS,
                cost_usd=COST_USD,
                cached=False,
                latency_ms=-1,
                timestamp=FIXED_TIME,
                stage="a",
            ),
            LLMUsage(
                model=TEST_MODEL,
                input_tokens=INPUT_TOKENS,
                output_tokens=OUTPUT_TOKENS,
                cost_usd=COST_USD,
                cached=False,
                latency_ms=LATENCY_MS,
                timestamp=FIXED_TIME,
                stage="x",  # type: ignore[arg-type]
            ),
        ],
    )
    async def test_record_rejects_invalid_usage_metrics(
        self,
        contract_store,
        usage: LLMUsage,
    ):
        """record_llm_usage should rely on DB checks for invalid metrics."""
        with pytest.raises(asyncpg.CheckViolationError):
            await contract_store.record_llm_usage(usage)

    async def test_record_usage_with_cost_rolls_back_cost_on_usage_rejection(
        self,
        contract_store,
    ):
        """Combined usage+cost writes should not leave orphan ledger spend."""
        usage = LLMUsage(
            model=TEST_MODEL,
            input_tokens=-1,
            output_tokens=OUTPUT_TOKENS,
            cost_usd=COST_USD,
            cached=False,
            latency_ms=LATENCY_MS,
            timestamp=FIXED_TIME,
            stage="a",
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await contract_store.record_llm_usage_with_cost(
                day=ROLLBACK_COST_DAY,
                spent_usd=COST_USD,
                usage=usage,
            )

        assert await contract_store.get_cost(ROLLBACK_COST_DAY) is None
