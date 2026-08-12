"""Identity-gated execution and raw verification for scratch mutations."""

from __future__ import annotations

import asyncio

from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration._pg_scratch_mutations import (
    ExpectedStageAState,
    ScratchMutationConfig,
    ScratchMutationResult,
    ScratchMutationTarget,
    assert_disposable_scratch_identity,
    run_scratch_mutation_workloads,
)
from jobfeed.adapters.store.postgres import PostgresStore

_PLATFORM = "jobfeed-benchmark"


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _natural_key_counts(
    reader: PostgresBaselineReader, canonical_ids: tuple[str, ...]
) -> dict[str, int]:
    rows = reader.rows(
        "SELECT canonical_id, COUNT(*) AS row_count FROM jobs "
        "WHERE platform=%s AND canonical_id=ANY(%s) GROUP BY canonical_id",
        (_PLATFORM, list(canonical_ids)),
    )
    return {
        str(row["canonical_id"]): _integer(row["row_count"], "natural-key count")
        for row in rows
    }


def _stage_a_states(
    reader: PostgresBaselineReader, job_ids: tuple[str, ...]
) -> dict[str, ExpectedStageAState]:
    rows = reader.rows(
        "SELECT job_id, stage_a_status, stage_a_error, stage_a_score "
        "FROM evaluations WHERE job_id=ANY(%s) ORDER BY job_id",
        ([int(job_id) for job_id in job_ids],),
    )
    return {
        str(row["job_id"]): ExpectedStageAState(
            status=str(row["stage_a_status"]) if row["stage_a_status"] else None,
            error=str(row["stage_a_error"]) if row["stage_a_error"] else None,
            score=(
                _integer(row["stage_a_score"], "stage_a_score")
                if row["stage_a_score"] is not None
                else None
            ),
        )
        for row in rows
    }


def validate_scratch_mutation_persistence(
    reader: PostgresBaselineReader, result: ScratchMutationResult
) -> None:
    """Require one upgraded scan row and exact evaluate states per fixture.

    Time complexity is O(scan fixtures + evaluate fixtures).

    Args:
        reader: Fresh read-only transaction on the scratch database.
        result: Expected identities from the mutation run.

    Raises:
        ValueError: If any row is missing, duplicated, or in the wrong state.
    """
    expected_counts = dict.fromkeys(result.scan_canonical_ids, 1)
    if _natural_key_counts(reader, result.scan_canonical_ids) != expected_counts:
        raise ValueError("scan scratch natural-key row count mismatch")
    actual = _stage_a_states(reader, tuple(result.expected_evaluation_states))
    if actual != result.expected_evaluation_states:
        raise ValueError("evaluate scratch persisted state mismatch")


async def _run_connected(
    dsn: str, config: ScratchMutationConfig
) -> ScratchMutationResult:
    store = PostgresStore(dsn, min_size=1, max_size=2)
    await store.connect()
    try:
        return await run_scratch_mutation_workloads(store, config)
    finally:
        await store.close()


def run_pg_scratch_mutation_benchmarks(
    target: ScratchMutationTarget, config: ScratchMutationConfig
) -> ScratchMutationResult:
    """Identity-gate, run, and verify real writes on disposable scratch only.

    Args:
        target: Explicit DSN and attested source/scratch identities.
        config: Fixture namespace and sample controls.

    Returns:
        Verified scratch mutation timings and identities.

    Raises:
        ValueError: If identity or persistence verification fails.
    """
    with PostgresBaselineReader(target.dsn) as reader:
        assert_disposable_scratch_identity(
            live_identity=reader.database_identity(),
            expected_identity=target.expected_database_identity,
            source_identity=target.source_database_identity,
        )
    result = asyncio.run(_run_connected(target.dsn, config))
    with PostgresBaselineReader(target.dsn) as reader:
        assert_disposable_scratch_identity(
            live_identity=reader.database_identity(),
            expected_identity=target.expected_database_identity,
            source_identity=target.source_database_identity,
        )
        validate_scratch_mutation_persistence(reader, result)
    return result
