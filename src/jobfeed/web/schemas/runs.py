"""DTOs for ``GET /api/runs`` and ``GET /api/runs/{run_id}``."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jobfeed.domain.models import PipelineRun


class RunSummary(BaseModel):
    """One pipeline run's identity and counters.

    Counters only — ``dry_run_preview`` never rides along on the wire.
    """

    run_id: str
    started_at: datetime
    source: str
    status: str
    jobs_discovered: int
    jobs_inserted: int
    jobs_updated: int
    jobs_filtered: int
    jobs_ml_gated: int
    jobs_seniority_filtered: int
    stage_a_scored: int
    stage_b_scored: int
    jobs_scored: int
    total_llm_cost_usd: float
    errors: int
    finished_at: datetime | None
    failure_code: str | None = None
    failure_message: str | None = None
    failed_stage: str | None = None
    failed_source: str | None = None
    last_progress_at: datetime | None = None
    restart_count: int = 0
    restarted_by_run_id: str | None = None
    progress_stage: str | None = None
    evaluate_stage: str | None = None
    evaluation_scope: str | None = None
    evaluation_input_total: int | None = None
    ml_gate_total: int | None = None
    ml_gate_processed: int = 0
    stage_a_total: int | None = None
    stage_a_processed: int = 0
    stage_b_total: int | None = None
    stage_b_processed: int = 0
    scan_source: str | None = None
    scan_phase: str | None = None
    scan_total: int | None = None
    scan_processed: int = 0
    scan_current_job_id: str | None = None
    scan_stats: dict[str, dict[str, int]] = Field(default_factory=dict)
    progress_updated_at: datetime | None = None


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
        status=run.status,
        jobs_discovered=run.jobs_discovered,
        jobs_inserted=run.jobs_inserted,
        jobs_updated=run.jobs_updated,
        jobs_filtered=run.jobs_filtered,
        jobs_ml_gated=run.jobs_ml_gated,
        jobs_seniority_filtered=run.jobs_seniority_filtered,
        stage_a_scored=run.stage_a_scored,
        stage_b_scored=run.stage_b_scored,
        jobs_scored=run.jobs_scored,
        total_llm_cost_usd=run.total_llm_cost_usd,
        errors=run.errors,
        finished_at=run.finished_at,
        failure_code=run.failure_code,
        failure_message=run.failure_message,
        failed_stage=run.failed_stage,
        failed_source=run.failed_source,
        last_progress_at=run.last_progress_at,
        restart_count=run.restart_count,
        restarted_by_run_id=run.restarted_by_run_id,
        progress_stage=run.progress_stage,
        evaluate_stage=run.evaluate_stage,
        evaluation_scope=run.evaluation_scope,
        evaluation_input_total=run.evaluation_input_total,
        ml_gate_total=run.ml_gate_total,
        ml_gate_processed=run.ml_gate_processed,
        stage_a_total=run.stage_a_total,
        stage_a_processed=run.stage_a_processed,
        stage_b_total=run.stage_b_total,
        stage_b_processed=run.stage_b_processed,
        scan_source=run.scan_source,
        scan_phase=run.scan_phase,
        scan_total=run.scan_total,
        scan_processed=run.scan_processed,
        scan_current_job_id=run.scan_current_job_id,
        scan_stats=run.scan_stats,
        progress_updated_at=run.progress_updated_at,
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
