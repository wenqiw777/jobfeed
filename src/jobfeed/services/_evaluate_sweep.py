"""Stage B sweep: retry failed Stage B jobs with the sweep model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from jobfeed.domain.models import JobPosting, PipelineRun
from jobfeed.services._evaluate_helpers import load_stage_a_scores, require_job_id
from jobfeed.services.evaluate_types import EvaluateDependencies

ScoreStageB = Callable[..., Awaitable[str]]


async def sweep_stage_b(
    deps: EvaluateDependencies,
    run: PipelineRun,
    failed: list[JobPosting],
    *,
    score_stage_b: ScoreStageB,
) -> None:
    """Retry failed Stage B jobs with the sweep model, else record errors.

    Args:
        deps: Evaluation dependencies (sweep LLM + store).
        run: Pipeline run whose error counter is updated.
        failed: Jobs whose primary Stage B pass failed.
        score_stage_b: Bound scorer reused for the sweep pass.
    """
    sweep = deps.llm_stage_b_sweep
    if sweep is None:
        for job in failed:
            jid = require_job_id(job)
            await deps.store.save_stage_b_error(jid, "stage_b_failed_no_sweep")
            run.errors += 1
        return
    stage_a_scores = await load_stage_a_scores(deps.store, failed)
    for job in failed:
        jid = require_job_id(job)
        outcome = await score_stage_b(
            job, run, sweep, stage_a_score=stage_a_scores.get(jid), parse_attempts=1
        )
        if outcome != "completed":
            await deps.store.save_stage_b_error(jid, "stage_b_sweep_failed")
            run.errors += 1
