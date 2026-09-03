"""Evaluation service that runs Stage A and Stage B through LLM ports."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from jobfeed.domain.errors import RunLeaseLostError, ScoringParseError
from jobfeed.domain.models import JobPosting, LLMRequest, PipelineRun, StageAResult
from jobfeed.domain.scoring_parse import parse_stage_a_response
from jobfeed.observability import JobfeedLogger, bind_run_id, get_tracer
from jobfeed.ports.prompts import PromptBundle
from jobfeed.ports.run_leases import RunLeaseStore
from jobfeed.services._evaluate_budget import EvaluateBudgetGate
from jobfeed.services._evaluate_claims import (
    load_stage_a_for_run,
    release_stage_a_for_run,
    validate_evaluate_stage,
)
from jobfeed.services._evaluate_dryrun import DryRunRequest, build_dry_run_preview
from jobfeed.services._evaluate_funnel import run_funnel
from jobfeed.services._evaluate_helpers import (
    SHORT_JD_THRESHOLD,
    UsageRecordContext,
    record_usage,
    require_job_id,
    run_auto_decay,
)
from jobfeed.services._evaluate_stage_b import _run_stage_b
from jobfeed.services._timing import StepTimer, get_perf_store
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig
from jobfeed.services.run_orchestration import RunLeaseOrchestrator, RunLeaseSession


class EvaluateService:
    """Application service for pending Stage A and Stage B evaluation."""

    def __init__(
        self,
        *,
        deps: EvaluateDependencies,
        config: EvaluateRuntimeConfig,
        logger: JobfeedLogger,
        run_orchestrator: RunLeaseOrchestrator | None = None,
    ) -> None:
        self._deps = deps
        self._config = config
        self._logger = logger
        self._budget = EvaluateBudgetGate(deps.store_ops, config.llm, logger)
        self._perf = get_perf_store(deps.store)
        self._tracer = get_tracer("jobfeed.evaluate")
        self._on_progress: Callable[[PipelineRun], None] | None = None
        self._run_orchestrator = run_orchestrator or RunLeaseOrchestrator(
            cast(RunLeaseStore, deps.store)
        )

    async def run(  # noqa: PLR0913 - on_progress + run params
        self,
        *,
        stage: str = "both",
        corpus: str = "unrated",
        limit: int | None = None,
        max_days: int | None = None,
        dry_run: bool = False,
        job_ids: list[str] | None = None,
        on_progress: Callable[[PipelineRun], None] | None = None,
        run: PipelineRun | None = None,
        lease_session: RunLeaseSession | None = None,
    ) -> PipelineRun:
        """Evaluate pending jobs, persist run counters.
        Args: stage "both"/"a"/"b"; corpus/limit/max_days/dry_run knobs;
            on_progress fires after funnel and each stage; run is an
            optional in-memory dry run; lease_session is a pre-acquired web
            fence, while direct real calls acquire their own fence.
        Returns: Recorded pipeline run with counters.
        Raises: Whatever a stage raised, after marking the run failed.
        """
        validate_evaluate_stage(stage)
        lim = self._config.default_eval_limit if limit is None else limit
        if dry_run:
            if lease_session is not None:
                raise ValueError("dry-run must not receive a lease session")
            if run is None:
                run = self._run_orchestrator.new_unpersisted_run("evaluate")
            bind_run_id(run.run_id)
            request = DryRunRequest(self._logger, stage, corpus, lim, max_days, job_ids)
            await build_dry_run_preview(self._deps, self._config, run, request)
            run.jobs_scored = run.stage_a_scored + run.stage_b_scored
            self._run_orchestrator.finish_unpersisted(run, "succeeded")
            if on_progress is not None:
                on_progress(run)
            return run
        if run is not None:
            raise ValueError("persisted runs require a lease session, not a bare run")
        if lease_session is None:
            result = await self._run_orchestrator.run(
                "evaluate",
                "evaluate",
                lambda session: self._run_leased(
                    session,
                    stage=stage,
                    corpus=corpus,
                    limit=lim,
                    max_days=max_days,
                    job_ids=job_ids,
                    on_progress=on_progress,
                ),
            )
            if on_progress is not None:
                on_progress(result)
            return result
        await self._run_leased(
            lease_session,
            stage=stage,
            corpus=corpus,
            limit=lim,
            max_days=max_days,
            job_ids=job_ids,
            on_progress=on_progress,
        )
        return lease_session.run

    async def _run_leased(  # noqa: PLR0913 - mirrors evaluate run filters
        self,
        lease_session: RunLeaseSession,
        *,
        stage: str,
        corpus: str,
        limit: int,
        max_days: int | None,
        job_ids: list[str] | None,
        on_progress: Callable[[PipelineRun], None] | None,
    ) -> None:
        """Execute evaluation work under an already-started fencing token."""
        run = lease_session.run
        bind_run_id(run.run_id)
        self._on_progress = on_progress
        run.evaluate_stage = stage
        run.progress_stage = "preparing"
        self._emit_progress(run)
        lease_session.ensure_active()
        await run_auto_decay(self._deps, self._config, self._logger)
        lease_session.ensure_active()
        if stage != "b":
            await self._run_stage_a(
                run, corpus, limit, max_days, job_ids, lease_session
            )
        if stage != "a":
            async with self._st(run.run_id, "stage", "stage_b"):
                await _run_stage_b(
                    self,
                    run,
                    limit,
                    max_days,
                    lease_session,
                    job_ids=job_ids,
                )
        run.jobs_scored = run.stage_a_scored + run.stage_b_scored
        run.progress_stage = "finalizing"
        self._emit_progress(run)

    def _emit_progress(self, run: PipelineRun) -> None:
        run.jobs_scored = run.stage_a_scored + run.stage_b_scored
        run.progress_updated_at = datetime.now(UTC)
        if self._on_progress is not None:
            self._on_progress(run)

    def _st(self, run_id: str, step_type: str, step_name: str) -> StepTimer:
        return StepTimer(self._perf, run_id, step_type, step_name, self._tracer)

    async def _run_stage_a(  # noqa: PLR0913 - scoped funnel inputs plus lease
        self,
        run: PipelineRun,
        corpus: str,
        limit: int,
        max_days: int | None,
        job_ids: list[str] | None,
        lease_session: RunLeaseSession,
    ) -> None:
        if limit <= 0:
            return  # "max jobs"=0 means do no funnel work (mirrors _run_stage_b)
        lease_session.ensure_active()
        has_budget = await self._budget.has_budget()
        lease_session.ensure_active()
        if not has_budget:
            return
        run.progress_stage = "ml_gate"
        self._emit_progress(run)
        async with self._st(run.run_id, "stage", "funnel"):
            survivors = await run_funnel(
                self._deps,
                self._config,
                run,
                corpus,
                max_days,
                job_ids=job_ids,
                logger=self._logger,
                dry_run=False,
                on_progress=lambda: self._emit_progress(run),
            )
        lease_session.ensure_active()
        self._emit_progress(run)
        if not survivors:
            run.progress_stage = "stage_a"
            run.stage_a_total = 0
            self._emit_progress(run)
            return
        async with self._st(run.run_id, "stage", "stage_a"):
            jobs = await load_stage_a_for_run(
                self._deps.store, corpus, limit, max_days, survivors
            )
            lease_session.ensure_active()
            run.progress_stage = "stage_a"
            run.stage_a_total = len(jobs)
            run.stage_a_processed = 0
            self._emit_progress(run)
            self._logger.info("stage_a_queued", count=len(jobs))
            sem = asyncio.Semaphore(max(1, self._config.llm.max_concurrent))

            async def _worker(job: JobPosting) -> None:
                async with sem:
                    lease_session.ensure_active()
                    await self._score_stage_a(job, run, lease_session)
                    run.stage_a_processed += 1
                    self._emit_progress(run)

            await asyncio.gather(*(_worker(j) for j in jobs))

    async def _score_stage_a(
        self,
        job: JobPosting,
        run: PipelineRun,
        lease_session: RunLeaseSession,
    ) -> None:
        lease_session.ensure_active()
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
        result = await self._call_parse_a(job_id, req, bundle, run, lease_session)
        if result is None:
            return
        lease_session.ensure_active()
        await self._deps.store.save_stage_a(job_id, result)
        lease_session.ensure_active()
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
        lease_session: RunLeaseSession,
    ) -> StageAResult | None:
        for attempt in range(2):
            lease_session.ensure_active()
            ledger_day = await self._budget.reserve()
            lease_session.ensure_active()
            if ledger_day is None:
                await release_stage_a_for_run(self._deps.store, job_id)
                return None
            try:
                resp = await self._deps.llm_stage_a.complete(req)
                lease_session.ensure_active()
            except RunLeaseLostError:
                raise
            except Exception as exc:
                lease_session.ensure_active()
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
