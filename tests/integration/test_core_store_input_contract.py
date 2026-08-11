"""PostgreSQL golden contracts for shared core-store input validation."""

from __future__ import annotations

import pytest

from jobfeed.adapters.store.postgres import PostgresStore

pytestmark = pytest.mark.postgres

MALFORMED_JOB_ID = "not-a-decimal-id"


async def test_malformed_single_job_ids_raise_value_error_across_capabilities(
    store: PostgresStore,
) -> None:
    """Single-ID reads, writes, and claim operations share strict parsing."""
    with pytest.raises(ValueError):
        await store.get_job(MALFORMED_JOB_ID)
    with pytest.raises(ValueError):
        await store.save_stage_a_error(MALFORMED_JOB_ID, "boom")
    with pytest.raises(ValueError):
        await store.save_stage_b_error(MALFORMED_JOB_ID, "boom")
    with pytest.raises(ValueError):
        await store.mark_stage_b_skipped(MALFORMED_JOB_ID)
    with pytest.raises(ValueError):
        await store.release_stage_a_claim(MALFORMED_JOB_ID)
    with pytest.raises(ValueError):
        await store.release_stage_b_claim(MALFORMED_JOB_ID)
    with pytest.raises(ValueError):
        await store.refresh_stage_b_claim(MALFORMED_JOB_ID)


async def test_malformed_batch_read_id_aborts_the_operation(
    store: PostgresStore,
) -> None:
    """Strict batch reads reject malformed IDs rather than dropping them."""
    with pytest.raises(ValueError):
        await store.get_stage_a_scores(["1", MALFORMED_JOB_ID])
