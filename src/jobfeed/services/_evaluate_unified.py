"""Single-call canonical evaluation loop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from jobfeed.domain.errors import RunLeaseLostError, ScoringParseError
from jobfeed.domain.models import JobPosting, LLMRequest, PipelineRun
from jobfeed.domain.unified_evaluation_parse import (
    parse_unified_evaluation_response,
)
from jobfeed.ports.store_unified import StoreUnifiedEvaluationMixin
from jobfeed.services._evaluate_helpers import (
    SHORT_JD_THRESHOLD,
    UsageRecordContext,
    record_usage,
    require_job_id,
)

if TYPE_CHECKING:
    from jobfeed.ports.llm import LLMClient
    from jobfeed.services.evaluate import EvaluateService
    from jobfeed.services.run_orchestration import RunLeaseSession

EVALUATOR_VERSION = "unified-v1"


async def run_unified_evaluation(  # noqa: PLR0913 - explicit run filters
    service: EvaluateService,
    run: PipelineRun,
    *,
    corpus: str,
    limit: int,
    max_days: int | None,
    lease_session: RunLeaseSession,
) -> None:
    """Claim and evaluate jobs once using the canonical evidence contract.

    Args:
        service: Owning service with ports, budget, config, and progress hooks.
        run: Mutable run counters and status.
        corpus: Pending-work selection mode.
        limit: Maximum jobs to claim.
        max_days: Optional discovery freshness window.
        lease_session: Active run fence checked around external work.
    """
    if limit <= 0:
        return
    lease_session.ensure_active()
    if not await service._budget.has_budget():
        return
    store = cast(StoreUnifiedEvaluationMixin, service._deps.store)
    claim_token = f"{run.run_id}:{lease_session.generation}"
    jobs = await store.claim_pending_evaluations(
        evaluator_version=EVALUATOR_VERSION,
        claim_token=claim_token,
        corpus=corpus,
        limit=limit,
        max_days=max_days,
    )
    lease_session.ensure_active()
    run.progress_stage = "evaluation"
    service._emit_progress(run)
    service._logger.info("evaluation_queued", count=len(jobs))
    semaphore = asyncio.Semaphore(max(1, service._config.llm.max_concurrent))
    client = service._deps.llm_evaluator or service._deps.llm_stage_b

    async def _worker(job: JobPosting) -> None:
        async with semaphore:
            lease_session.ensure_active()
            if await _score_job(service, job, run, client, lease_session, claim_token):
                run.jobs_scored += 1
            service._emit_progress(run)

    await asyncio.gather(*(_worker(job) for job in jobs))


async def _score_job(  # noqa: PLR0913 - explicit worker dependencies
    service: EvaluateService,
    job: JobPosting,
    run: PipelineRun,
    client: LLMClient,
    lease_session: RunLeaseSession,
    claim_token: str,
) -> bool:
    job_id = require_job_id(job)
    store = cast(StoreUnifiedEvaluationMixin, service._deps.store)
    if len(job.jd_text or "") < SHORT_JD_THRESHOLD:
        await store.save_evaluation_error(
            job_id,
            f"jd_text_too_short: {len(job.jd_text or '')} chars",
            EVALUATOR_VERSION,
            claim_token,
        )
        run.errors += 1
        return False
    bundle = service._deps.prompt_renderer.render_unified(
        resume_text=service._config.resume_text,
        job=job,
    )
    model = service._config.llm.evaluator or service._config.llm.stage_b
    request = LLMRequest(messages=bundle.messages, model=model)
    for attempt in range(2):
        lease_session.ensure_active()
        ledger_day = await service._budget.reserve()
        if ledger_day is None:
            await store.release_evaluation_claim(job_id, EVALUATOR_VERSION, claim_token)
            return False
        try:
            response = await client.complete(request)
            lease_session.ensure_active()
        except RunLeaseLostError:
            raise
        except Exception as exc:
            if attempt == 0:
                service._logger.warning(
                    "evaluation_runtime_retry", job_id=job_id, error=str(exc)
                )
                continue
            await _save_error(service, job_id, str(exc), run, claim_token)
            return False
        await record_usage(
            service._deps.store_ops,
            response,
            UsageRecordContext(job_id, "evaluation", run.run_id, ledger_day),
        )
        run.total_llm_cost_usd += response.cost_usd or 0.0
        try:
            result = parse_unified_evaluation_response(
                response.content,
                model=response.model,
                prompt_hash=bundle.prompt_hash,
                resume_hash=bundle.resume_hash,
                evaluator_version=EVALUATOR_VERSION,
                cost_usd=response.cost_usd,
            )
        except ScoringParseError as exc:
            if attempt == 0:
                service._logger.warning(
                    "evaluation_parse_retry", job_id=job_id, error=str(exc)
                )
                continue
            await _save_error(service, job_id, str(exc), run, claim_token)
            return False
        lease_session.ensure_active()
        await store.save_evaluation(job_id, result, claim_token)
        service._logger.info(
            "evaluation_scored",
            job_id=job_id,
            score=result.match_score,
            match_tier=result.match_tier,
        )
        return True
    return False  # pragma: no cover


async def _save_error(
    service: EvaluateService,
    job_id: str,
    error: str,
    run: PipelineRun,
    claim_token: str,
) -> None:
    store = cast(StoreUnifiedEvaluationMixin, service._deps.store)
    await store.save_evaluation_error(job_id, error, EVALUATOR_VERSION, claim_token)
    run.errors += 1
    service._logger.error("evaluation_failed", job_id=job_id, error=error)


__all__ = ["EVALUATOR_VERSION", "run_unified_evaluation"]
