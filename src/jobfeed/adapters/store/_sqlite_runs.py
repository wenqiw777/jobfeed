"""Hydrate pipeline-run rows for the composed SQLite core store."""

from __future__ import annotations

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _parse_utc_timestamp,
)
from jobfeed.domain.models import PipelineRun


async def _get_pipeline_run(
    connection: aiosqlite.Connection,
    run_id: str,
) -> PipelineRun | None:
    row = await _fetch_row(
        connection,
        "SELECT * FROM pipeline_runs WHERE run_id=?",
        (run_id,),
    )
    if row is None:
        return None
    return _pipeline_run_from_row(row)


def _pipeline_run_from_row(row: aiosqlite.Row) -> PipelineRun:
    """Hydrate one complete pipeline-run row."""
    finished_at = row["finished_at"]
    return PipelineRun(
        run_id=str(row["run_id"]),
        started_at=_parse_utc_timestamp(row["started_at"]),
        source=str(row["source"]),
        status=str(row["status"]),
        jobs_discovered=int(row["jobs_discovered"]),
        jobs_inserted=int(row["jobs_inserted"]),
        jobs_updated=int(row["jobs_updated"]),
        jobs_filtered=int(row["jobs_filtered"]),
        jobs_ml_gated=int(row["jobs_ml_gated"]),
        jobs_gate_passed=int(row["jobs_gate_passed"]),
        stage_a_scored=int(row["stage_a_scored"]),
        stage_b_scored=int(row["stage_b_scored"]),
        jobs_scored=int(row["jobs_scored"]),
        total_llm_cost_usd=float(row["total_llm_cost_usd"]),
        errors=int(row["errors"]),
        finished_at=(
            _parse_utc_timestamp(finished_at) if finished_at is not None else None
        ),
    )
