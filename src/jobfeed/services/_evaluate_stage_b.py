"""Stage B scoring loop kept separate from EvaluateService orchestration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from jobfeed.domain.errors import RunLeaseLostError, ScoringParseError
from jobfeed.domain.models import JobPosting, LLMRequest, PipelineRun
from jobfeed.domain.scoring_parse import parse_stage_b_response
from jobfeed.ports.llm import LLMClient
from jobfeed.services._evaluate_claims import (
    load_stage_b_for_run,
    maintain_stage_b_claim,
    release_stage_b_for_run,
    sync_stage_b_threshold,
)
from jobfeed.services._evaluate_helpers import (
    UsageRecordContext,
    load_stage_a_scores,
    record_usage,
    require_job_id,
)
from jobfeed.services._evaluate_sweep import sweep_stage_b
from jobfeed.services.run_orchestration import RunLeaseSession

if TYPE_CHECKING:
    from jobfeed.services.evaluate import EvaluateService


async def _run_stage_b(  # noqa: PLR0913 - service context plus scoped run inputs
    service: EvaluateService,
    run: PipelineRun,
    limit: int,
    max_days: int | None,
    lease_session: RunLeaseSession,
    job_ids: list[str] | None = None,
) -> None:
    """Load, score, and sweep Stage B jobs under a scheduling fence."""
    if limit <= 0:
        return
    run.progress_stage = "stage_b"
    service._emit_progress(run)
    lease_session.ensure_active()
    await sync_stage_b_threshold(
        service._deps.store,
        service._config.stage_a_threshold,
        max_days,
        transition_sync=service._deps.stage_b_threshold_sync,
    )
    lease_session.ensure_active()
    has_budget = await service._budget.has_budget()
    lease_session.ensure_active()
    if not has_budget:
        return
    jobs = await load_stage_b_for_run(
        service._deps.store,
        limit,
        max_days,
        service._config.stage_a_threshold,
        job_ids,
    )
    lease_session.ensure_active()
    run.stage_b_total = len(jobs)
    run.stage_b_processed = 0
    service._emit_progress(run)
    service._logger.info("stage_b_queued", count=len(jobs))
    stage_a_scores = await load_stage_a_scores(service._deps.store, jobs)
    lease_session.ensure_active()
    sem = asyncio.Semaphore(max(1, service._config.llm.max_concurrent))
    failed: list[JobPosting] = []
    settled_job_ids: set[str] = set()

    async def _worker(job: JobPosting) -> None:
        async with sem:
            lease_session.ensure_active()
            score = stage_a_scores.get(require_job_id(job))
            outcome = await _score_stage_b(
                service,
                job,
                run,
                service._deps.llm_stage_b,
                lease_session,
                stage_a_score=score,
            )
            if outcome == "failed":
                failed.append(job)
            else:
                settled_job_ids.add(require_job_id(job))
            run.stage_b_processed += 1
            service._emit_progress(run)

    workers = [asyncio.create_task(_worker(job)) for job in jobs]
    try:
        await asyncio.gather(*workers)
        lease_session.ensure_active()

        async def _score_sweep(
            job: JobPosting,
            sweep_run: PipelineRun,
            llm: LLMClient,
            *,
            stage_a_score: int | None = None,
            parse_attempts: int = 2,
        ) -> str:
            outcome = await _score_stage_b(
                service,
                job,
                sweep_run,
                llm,
                lease_session,
                stage_a_score=stage_a_score,
                parse_attempts=parse_attempts,
            )
            if outcome != "failed":
                settled_job_ids.add(require_job_id(job))
            return outcome

        await sweep_stage_b(
            service._deps,
            run,
            failed,
            score_stage_b=_score_sweep,
        )
        settled_job_ids.update(require_job_id(job) for job in failed)
    except BaseException:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if not lease_session.lease_lost:
            unfinished = [
                job for job in jobs if require_job_id(job) not in settled_job_ids
            ]
            await asyncio.gather(
                *(
                    release_stage_b_for_run(service._deps.store, require_job_id(job))
                    for job in unfinished
                ),
                return_exceptions=True,
            )
        raise


async def _score_stage_b(  # noqa: PLR0913 - scorer inputs plus lease guard
    service: EvaluateService,
    job: JobPosting,
    run: PipelineRun,
    llm: LLMClient,
    lease_session: RunLeaseSession,
    *,
    stage_a_score: int | None = None,
    parse_attempts: int = 2,
) -> str:
    """Score one Stage B job without writing after lease ownership loss."""
    lease_session.ensure_active()
    job_id = require_job_id(job)
    bundle = service._deps.prompt_renderer.render_stage_b(
        resume_text=service._config.resume_text,
        job=job,
        stage_a_score=stage_a_score,
    )
    req = LLMRequest(messages=bundle.messages, model=service._config.llm.stage_b)
    for attempt in range(parse_attempts):
        lease_session.ensure_active()
        ledger_day = await service._budget.reserve()
        lease_session.ensure_active()
        if ledger_day is None:
            await release_stage_b_for_run(service._deps.store, job_id)
            return "skipped"
        try:
            async with maintain_stage_b_claim(service._deps.store, job_id):
                lease_session.ensure_active()
                resp = await llm.complete(req)
            lease_session.ensure_active()
        except RunLeaseLostError:
            raise
        except Exception as exc:
            lease_session.ensure_active()
            service._logger.error(
                "stage_b_runtime_failed", job_id=job_id, error=str(exc)
            )
            return "failed"
        context = UsageRecordContext(job_id, "b", run.run_id, ledger_day)
        await record_usage(service._deps.store_ops, resp, context)
        run.total_llm_cost_usd += resp.cost_usd or 0.0
        try:
            result = parse_stage_b_response(
                resp.content,
                model=resp.model,
                prompt_hash=bundle.prompt_hash,
                resume_hash=bundle.resume_hash,
                cost_usd=resp.cost_usd,
            )
        except ScoringParseError as exc:
            if attempt + 1 < parse_attempts:
                service._logger.warning(
                    "stage_b_parse_retry", job_id=job_id, error=str(exc)
                )
                continue
            return "failed"
        lease_session.ensure_active()
        await service._deps.store.save_stage_b(job_id, result)
        run.stage_b_scored += 1
        service._logger.info(
            "stage_b_scored", job_id=job_id, score=result.fit_analysis.score
        )
        return "completed"
    return "failed"  # pragma: no cover


__all__ = ["_run_stage_b"]
