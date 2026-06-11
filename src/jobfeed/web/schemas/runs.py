"""DTOs for ``GET /api/runs`` and ``GET /api/runs/{run_id}``."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jobfeed.domain.models import PipelineRun


class RunSummary(BaseModel):
    """One pipeline run's identity and counters.

    Counters only — ``dry_run_preview`` never rides along on the wire.
    """

    run_id: str
    started_at: datetime
    source: str
    jobs_discovered: int
    jobs_inserted: int
    jobs_updated: int
    jobs_filtered: int
    jobs_ml_gated: int
    stage_a_scored: int
    stage_b_scored: int
    jobs_scored: int
    total_llm_cost_usd: float
    errors: int
    finished_at: datetime | None


class RunsListResponse(BaseModel):
    """``GET /api/runs`` response: the requested window plus the total."""

    runs: list[RunSummary]
    total: int


def run_summary(run: PipelineRun) -> RunSummary:
    """Map one pipeline run to its counters-only DTO.

    Args:
        run: Domain pipeline run.

    Returns:
        Wire-shape run summary.
    """
    return RunSummary(
        run_id=run.run_id,
        started_at=run.started_at,
        source=run.source,
        jobs_discovered=run.jobs_discovered,
        jobs_inserted=run.jobs_inserted,
        jobs_updated=run.jobs_updated,
        jobs_filtered=run.jobs_filtered,
        jobs_ml_gated=run.jobs_ml_gated,
        stage_a_scored=run.stage_a_scored,
        stage_b_scored=run.stage_b_scored,
        jobs_scored=run.jobs_scored,
        total_llm_cost_usd=run.total_llm_cost_usd,
        errors=run.errors,
        finished_at=run.finished_at,
    )


def runs_list_response(runs: list[PipelineRun], total: int) -> RunsListResponse:
    """Render a runs window and its total as the list response.

    Args:
        runs: Pipeline runs, newest first.
        total: All-time run count (ignores the window).

    Returns:
        Wire-shape runs list.
    """
    return RunsListResponse(runs=[run_summary(run) for run in runs], total=total)


__all__ = ["RunSummary", "RunsListResponse", "run_summary", "runs_list_response"]
