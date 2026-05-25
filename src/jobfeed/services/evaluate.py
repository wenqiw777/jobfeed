"""Evaluation service that runs Stage A and Stage B through LLM ports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.config import LLMSettings
from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import JobPosting, LLMRequest, PipelineRun, StageAResult
from jobfeed.domain.scoring_parse import (
    parse_stage_a_response,
    parse_stage_b_response,
)
from jobfeed.observability import JobfeedLogger, bind_run_id
from jobfeed.ports.llm import LLMClient
from jobfeed.ports.prompts import PromptBundle, PromptRenderer
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services._evaluate_helpers import (
    SHORT_JD_THRESHOLD,
    check_budget,
    load_stage_a,
    log_dry_run,
    record_usage,
    require_job_id,
)
from jobfeed.services.runs import start_pipeline_run


@dataclass(frozen=True, kw_only=True)
class EvaluateDependencies:
    """Ports used by EvaluateService."""

    store: JobStore
    store_ops: StoreOpsMixin
    prompt_renderer: PromptRenderer
    llm_stage_a: LLMClient
    llm_stage_b: LLMClient
    llm_stage_b_sweep: LLMClient | None = None


@dataclass(frozen=True, kw_only=True)
class EvaluateRuntimeConfig:
    """Runtime knobs used by EvaluateService."""

    llm: LLMSettings
    stage_a_threshold: int
    resume_text: str


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
            stage: "both", "a", or "b".
            corpus: "unrated", "all", or "failed".
            limit: Max jobs per stage.
            max_days: Freshness filter.
            dry_run: Skip LLM calls.

        Returns:
            Recorded pipeline run with counters.
        """
        run = start_pipeline_run("evaluate")
        bind_run_id(run.run_id)
        lim = limit or 100
        if dry_run:
            await self._dry_run(stage, corpus, lim, max_days)
        else:
            await self._real_run(run, stage, corpus, lim, max_days)
        run.jobs_scored = run.stage_a_scored + run.stage_b_scored
        run.finished_at = datetime.now(UTC)
        await self._deps.store.record_pipeline_run(run)
        return run

    async def _dry_run(self, stg: str, corp: str, lim: int, md: int | None) -> None:
        if stg != "b":
            log_dry_run(
                self._logger,
                "stage_a",
                await load_stage_a(self._deps.store, corp, lim, md),
            )
        if stg != "a":
            log_dry_run(
                self._logger,
                "stage_b",
                await self._deps.store.load_pending_stage_b(limit=lim, max_days=md),
            )

    async def _real_run(
        self, run: PipelineRun, stg: str, corp: str, lim: int, md: int | None
    ) -> None:
        if stg != "b":
            await self._run_stage_a(run, corp, lim, md)
        if stg != "a":
            await self._run_stage_b(run, lim, md)

    async def _run_stage_a(
        self, run: PipelineRun, corpus: str, limit: int, max_days: int | None
    ) -> None:
        jobs = await load_stage_a(self._deps.store, corpus, limit, max_days)
        if not await self._budget_ok():
            return
        sem = asyncio.Semaphore(max(1, self._config.llm.max_concurrent))

        async def _worker(job: JobPosting) -> None:
            async with sem:
                await self._score_stage_a(job, run)

        await asyncio.gather(*(_worker(j) for j in jobs))

    async def _score_stage_a(self, job: JobPosting, run: PipelineRun) -> None:
        job_id = require_job_id(job)
        if not await self._budget_ok():
            return
        if len(job.jd_text or "") < SHORT_JD_THRESHOLD:
            await self._deps.store.save_stage_a_error(
                job_id,
                f"jd_text_too_short: {len(job.jd_text or '')} chars",
            )
            run.errors += 1
            return
        bundle = self._deps.prompt_renderer.render_stage_a(
            resume_text=self._config.resume_text,
            job=job,
        )
        req = LLMRequest(messages=bundle.messages, model=self._config.llm.stage_a)
        result = await self._call_parse_a(job_id, req, bundle, run)
        if result is None:
            return
        await self._deps.store.save_stage_a(job_id, result)
        run.stage_a_scored += 1
        self._logger.info("stage_a_scored", job_id=job_id, score=result.score)
        run.total_llm_cost_usd += result.cost_usd or 0.0
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
            try:
                resp = await self._deps.llm_stage_a.complete(req)
            except Exception as exc:
                await self._deps.store.save_stage_a_error(job_id, str(exc))
                run.errors += 1
                self._logger.error(
                    "stage_a_runtime_failed", job_id=job_id, error=str(exc)
                )
                return None
            await record_usage(
                self._deps.store_ops, resp, job_id, "stage_a", run.run_id
            )
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
        jobs = await self._deps.store.load_pending_stage_b(
            limit=limit, max_days=max_days
        )
        sem = asyncio.Semaphore(max(1, self._config.llm.max_concurrent))
        failed: list[JobPosting] = []

        async def _worker(job: JobPosting) -> None:
            async with sem:
                ok = await self._score_stage_b(job, run, self._deps.llm_stage_b)
                if not ok:
                    failed.append(job)

        await asyncio.gather(*(_worker(j) for j in jobs))
        await self._sweep_stage_b(failed, run)

    async def _score_stage_b(
        self,
        job: JobPosting,
        run: PipelineRun,
        llm: LLMClient,
    ) -> bool:
        job_id = require_job_id(job)
        bundle = self._deps.prompt_renderer.render_stage_b(
            resume_text=self._config.resume_text,
            job=job,
        )
        req = LLMRequest(messages=bundle.messages, model=self._config.llm.stage_b)
        for attempt in range(2):
            try:
                resp = await llm.complete(req)
            except Exception as exc:
                self._logger.error(
                    "stage_b_runtime_failed", job_id=job_id, error=str(exc)
                )
                return False
            await record_usage(
                self._deps.store_ops, resp, job_id, "stage_b", run.run_id
            )
            try:
                result = parse_stage_b_response(
                    resp.content,
                    model=resp.model,
                    prompt_hash=bundle.prompt_hash,
                    resume_hash=bundle.resume_hash,
                    cost_usd=resp.cost_usd,
                )
            except ScoringParseError as exc:
                if attempt == 0:
                    self._logger.warning(
                        "stage_b_parse_retry", job_id=job_id, error=str(exc)
                    )
                    continue
                return False
            await self._deps.store.save_stage_b(job_id, result)
            run.stage_b_scored += 1
            self._logger.info(
                "stage_b_scored", job_id=job_id, score=result.fit_analysis.score
            )
            run.total_llm_cost_usd += result.cost_usd or 0.0
            return True
        return False  # pragma: no cover

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
        for job in failed:
            ok = await self._score_stage_b(job, run, sweep)
            if not ok:
                jid = require_job_id(job)
                await self._deps.store.save_stage_b_error(jid, "stage_b_sweep_failed")
                run.errors += 1

    async def _budget_ok(self) -> bool:
        return await check_budget(
            self._deps.store_ops,
            self._config.llm.max_daily_score_calls,
            self._config.llm.max_daily_cost_usd,
            self._logger,
        )


__all__ = ["EvaluateDependencies", "EvaluateRuntimeConfig", "EvaluateService"]
