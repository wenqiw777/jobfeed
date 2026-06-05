"""Evaluation service that runs Stage A and Stage B through LLM ports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import JobPosting, LLMRequest, PipelineRun, StageAResult
from jobfeed.domain.scoring_parse import parse_stage_a_response, parse_stage_b_response
from jobfeed.observability import JobfeedLogger, bind_run_id
from jobfeed.ports.llm import LLMClient
from jobfeed.ports.prompts import PromptBundle
from jobfeed.services._evaluate_budget import EvaluateBudgetGate
from jobfeed.services._evaluate_claims import (
    load_stage_a_for_run,
    load_stage_b_for_run,
    maintain_stage_b_claim,
    release_stage_a_for_run,
    release_stage_b_for_run,
    sync_stage_b_threshold,
    validate_evaluate_stage,
)
from jobfeed.services._evaluate_funnel import run_funnel
from jobfeed.services._evaluate_helpers import (
    SHORT_JD_THRESHOLD,
    DryRunRequest,
    UsageRecordContext,
    build_dry_run_preview,
    load_stage_a_scores,
    record_usage,
    require_job_id,
)
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig
from jobfeed.services.runs import start_pipeline_run


class EvaluateService:
    """Application service for pending Stage A and Stage B evaluation."""

    def __init__(
        self,
        *,
        deps: EvaluateDependencies,
        config: EvaluateRuntimeConfig,
        logger: JobfeedLogger,
    ) -> None:
        self._deps = deps
        self._config = config
        self._logger = logger
        self._budget = EvaluateBudgetGate(deps.store_ops, config.llm, logger)

    async def run(
        self,
        *,
        stage: str = "both",
        corpus: str = "unrated",
        limit: int | None = None,
        max_days: int | None = None,
        dry_run: bool = False,
    ) -> PipelineRun:
        """Evaluate pending jobs and persist run counters.

        Args:
            stage: "both"/"a"/"b"; corpus: "unrated"/"all"/"failed"; limit caps
                jobs per stage; max_days filters freshness; dry_run skips LLM calls.

        Returns:
            Recorded pipeline run with counters.
        """
        validate_evaluate_stage(stage)
        run = start_pipeline_run("evaluate")
        bind_run_id(run.run_id)
        lim = 100 if limit is None else limit
        if dry_run:
            request = DryRunRequest(self._logger, stage, corpus, lim, max_days)
            await build_dry_run_preview(self._deps, self._config, run, request)
        else:
            if stage != "b":
                await self._run_stage_a(run, corpus, lim, max_days)
            if stage != "a":
                await self._run_stage_b(run, lim, max_days)
        run.jobs_scored = run.stage_a_scored + run.stage_b_scored
        run.finished_at = datetime.now(UTC)
        if not dry_run:
            await self._deps.store.record_pipeline_run(run)
        return run

    async def _run_stage_a(
        self, run: PipelineRun, corpus: str, limit: int, max_days: int | None
    ) -> None:
        if limit <= 0:
            return  # "max jobs"=0 means do no funnel work (mirrors _run_stage_b)
        # Budget BEFORE the funnel: an exhausted budget runs no Stage A call, so
        # the candidate load + ML gate would be wasted (Stage B gates first too).
        if not await self._budget.has_budget():
            return
        survivors = await run_funnel(
            self._deps,
            self._config,
            run,
            corpus,
            max_days,
            logger=self._logger,
            dry_run=False,
        )
        if not survivors:
            return
        jobs = await load_stage_a_for_run(
            self._deps.store, corpus, limit, max_days, survivors
        )
        sem = asyncio.Semaphore(max(1, self._config.llm.max_concurrent))

        async def _worker(job: JobPosting) -> None:
            async with sem:
                await self._score_stage_a(job, run)

        await asyncio.gather(*(_worker(j) for j in jobs))

    async def _score_stage_a(self, job: JobPosting, run: PipelineRun) -> None:
        job_id = require_job_id(job)
        if len(job.jd_text or "") < SHORT_JD_THRESHOLD:
            await self._deps.store.save_stage_a_error(
                job_id, f"jd_text_too_short: {len(job.jd_text or '')} chars"
            )
            run.errors += 1
            return
        bundle = self._deps.prompt_renderer.render_stage_a(
            resume_text=self._config.resume_text, job=job
        )
        req = LLMRequest(messages=bundle.messages, model=self._config.llm.stage_a)
        result = await self._call_parse_a(job_id, req, bundle, run)
        if result is None:
            return
        await self._deps.store.save_stage_a(job_id, result)
        run.stage_a_scored += 1
        self._logger.info("stage_a_scored", job_id=job_id, score=result.score)
        if result.score < self._config.stage_a_threshold:
            await self._deps.store.mark_stage_b_skipped(job_id)

    async def _call_parse_a(
        self,
        job_id: str,
        req: LLMRequest,
        bundle: PromptBundle,
        run: PipelineRun,
    ) -> StageAResult | None:
        for attempt in range(2):
            ledger_day = await self._budget.reserve()
            if ledger_day is None:
                await release_stage_a_for_run(self._deps.store, job_id)
                return None
            try:
                resp = await self._deps.llm_stage_a.complete(req)
            except Exception as exc:
                if attempt == 0:
                    self._logger.warning(
                        "stage_a_runtime_retry", job_id=job_id, error=str(exc)
                    )
                    continue
                await self._deps.store.save_stage_a_error(job_id, str(exc))
                run.errors += 1
                self._logger.error(
                    "stage_a_runtime_failed", job_id=job_id, error=str(exc)
                )
                return None
            context = UsageRecordContext(job_id, "a", run.run_id, ledger_day)
            await record_usage(self._deps.store_ops, resp, context)
            run.total_llm_cost_usd += resp.cost_usd or 0.0
            try:
                return parse_stage_a_response(
                    resp.content,
                    model=resp.model,
                    prompt_hash=bundle.prompt_hash,
                    resume_hash=bundle.resume_hash,
                    cost_usd=resp.cost_usd,
                )
            except ScoringParseError as exc:
                if attempt == 0:
                    self._logger.warning(
                        "stage_a_parse_retry", job_id=job_id, error=str(exc)
                    )
                    continue
                await self._deps.store.save_stage_a_error(job_id, str(exc))
                run.errors += 1
                self._logger.error(
                    "stage_a_parse_failed", job_id=job_id, error=str(exc)
                )
                return None
        return None  # pragma: no cover

    async def _run_stage_b(
        self, run: PipelineRun, limit: int, max_days: int | None
    ) -> None:
        if limit <= 0:
            return
        await sync_stage_b_threshold(
            self._deps.store, self._config.stage_a_threshold, max_days
        )
        if not await self._budget.has_budget():
            return
        jobs = await load_stage_b_for_run(
            self._deps.store, limit, max_days, self._config.stage_a_threshold
        )
        stage_a_scores = await load_stage_a_scores(self._deps.store, jobs)
        sem = asyncio.Semaphore(max(1, self._config.llm.max_concurrent))
        failed: list[JobPosting] = []

        async def _worker(job: JobPosting) -> None:
            async with sem:
                score = stage_a_scores.get(require_job_id(job))
                outcome = await self._score_stage_b(
                    job, run, self._deps.llm_stage_b, stage_a_score=score
                )
                if outcome == "failed":
                    failed.append(job)

        await asyncio.gather(*(_worker(j) for j in jobs))
        await self._sweep_stage_b(failed, run)

    async def _score_stage_b(
        self,
        job: JobPosting,
        run: PipelineRun,
        llm: LLMClient,
        *,
        stage_a_score: int | None = None,
        parse_attempts: int = 2,
    ) -> str:
        job_id = require_job_id(job)
        bundle = self._deps.prompt_renderer.render_stage_b(
            resume_text=self._config.resume_text,
            job=job,
            stage_a_score=stage_a_score,
        )
        req = LLMRequest(messages=bundle.messages, model=self._config.llm.stage_b)
        for attempt in range(parse_attempts):
            ledger_day = await self._budget.reserve()
            if ledger_day is None:
                await release_stage_b_for_run(self._deps.store, job_id)
                return "skipped"
            try:
                async with maintain_stage_b_claim(self._deps.store, job_id):
                    resp = await llm.complete(req)
            except Exception as exc:
                self._logger.error(
                    "stage_b_runtime_failed", job_id=job_id, error=str(exc)
                )
                return "failed"
            context = UsageRecordContext(job_id, "b", run.run_id, ledger_day)
            await record_usage(self._deps.store_ops, resp, context)
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
                    self._logger.warning(
                        "stage_b_parse_retry", job_id=job_id, error=str(exc)
                    )
                    continue
                return "failed"
            await self._deps.store.save_stage_b(job_id, result)
            run.stage_b_scored += 1
            self._logger.info(
                "stage_b_scored", job_id=job_id, score=result.fit_analysis.score
            )
            return "completed"
        return "failed"  # pragma: no cover

    async def _sweep_stage_b(self, failed: list[JobPosting], run: PipelineRun) -> None:
        sweep = self._deps.llm_stage_b_sweep
        if sweep is None:
            for job in failed:
                jid = require_job_id(job)
                await self._deps.store.save_stage_b_error(
                    jid, "stage_b_failed_no_sweep"
                )
                run.errors += 1
            return
        stage_a_scores = await load_stage_a_scores(self._deps.store, failed)
        for job in failed:
            jid = require_job_id(job)
            outcome = await self._score_stage_b(
                job,
                run,
                sweep,
                stage_a_score=stage_a_scores.get(jid),
                parse_attempts=1,
            )
            if outcome != "completed":
                await self._deps.store.save_stage_b_error(jid, "stage_b_sweep_failed")
                run.errors += 1
