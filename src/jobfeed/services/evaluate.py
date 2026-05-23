"""Evaluation service that runs Stage A and Stage B through LLM ports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from jobfeed.config import Settings
from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import (
    JobPosting,
    LLMRequest,
    LLMResponse,
    PipelineRun,
    StageAResult,
    StageBResult,
)
from jobfeed.domain.scoring import (
    parse_stage_a_response,
    parse_stage_b_response,
    render_stage_a_prompt,
    render_stage_b_prompt,
)
from jobfeed.domain.types import StageName
from jobfeed.observability import JobfeedLogger, bind_run_id
from jobfeed.ports.llm import LLMClient
from jobfeed.ports.store import JobStore
from jobfeed.services.error_handler import ServiceErrorHandler
from jobfeed.services.runs import start_pipeline_run

# TODO(phase1): Replace skeleton prompt inputs with configured resume, rubric,
# and prompt hash sources once real scoring configuration lands.
SKELETON_HASH = "skeleton"
SKELETON_RESUME = "Phase 0 resume placeholder."
SKELETON_RUBRIC = "Prefer relevant backend, data, automation, and timing fit."
StageResult = StageAResult | StageBResult


class EvaluateService:
    """Application service for pending Stage A and Stage B evaluation."""

    def __init__(
        self,
        store: JobStore,
        llm: LLMClient,
        settings: Settings,
        logger: JobfeedLogger,
    ) -> None:
        """Create an evaluation service with injected ports.

        Args:
            store: Persistence port for pending jobs and results.
            llm: LLM completion port.
            settings: Runtime settings for models and thresholds.
            logger: Structured logger for evaluation events.
        """
        self.store = store
        self.llm = llm
        self.settings = settings
        self.logger = logger
        self.error_handler = ServiceErrorHandler(store=store, logger=logger)

    async def run(self, dry_run: bool = False) -> PipelineRun:
        """Evaluate pending jobs and persist run counters.

        Args:
            dry_run: When true, log pending jobs without calling the LLM.

        Returns:
            Recorded pipeline run with Stage A and Stage B counters.
        """
        run = start_pipeline_run("evaluate")
        bind_run_id(run.run_id)
        stage_a_jobs = await self.store.load_pending_stage_a()
        stage_b_jobs = await self.store.load_pending_stage_b()
        if dry_run:
            self._log_dry_run("stage_a", stage_a_jobs)
            self._log_dry_run("stage_b", stage_b_jobs)
        else:
            await self._score_stage_jobs("stage_a", stage_a_jobs, run)
            stage_b_jobs = await self.store.load_pending_stage_b()
            stage_b_jobs = await self._skip_below_threshold(stage_b_jobs)
            await self._score_stage_jobs("stage_b", stage_b_jobs, run)
        run.jobs_scored = run.stage_a_scored + run.stage_b_scored
        run.finished_at = datetime.now(UTC)
        await self.store.record_pipeline_run(run)
        return run

    async def _skip_below_threshold(self, jobs: list[JobPosting]) -> list[JobPosting]:
        """Skip pending Stage B rows whose Stage A score is below threshold.

        The save_stage_a path skips below-threshold jobs scored in this run, but
        rows that became Stage A-completed without it (legacy import, or scored
        before the gate existed) still surface in the Stage B queue. Mark those
        skipped here so they never reach Stage B (plan Decision 1).

        Args:
            jobs: Stage B pending jobs.

        Returns:
            Jobs whose Stage A score meets the threshold.
        """
        threshold = self.settings.scoring.stage_a_threshold
        eligible: list[JobPosting] = []
        for job in jobs:
            job_id = _require_job_id(job)
            evaluation = await self.store.get_evaluation(job_id)
            score = (
                evaluation.stage_a.score
                if evaluation is not None and evaluation.stage_a is not None
                else None
            )
            if score is not None and score < threshold:
                await self.store.mark_stage_b_skipped(job_id)
            else:
                eligible.append(job)
        return eligible

    async def _score_stage_jobs(
        self,
        stage: StageName,
        jobs: list[JobPosting],
        run: PipelineRun,
    ) -> None:
        semaphore = asyncio.Semaphore(max(1, self.settings.llm.max_concurrent))

        async def score_with_limit(job: JobPosting) -> None:
            async with semaphore:
                await self._score_stage_job(stage, job, run)

        await asyncio.gather(*(score_with_limit(job) for job in jobs))

    async def _score_stage_job(
        self,
        stage: StageName,
        job: JobPosting,
        run: PipelineRun,
    ) -> None:
        job_id = _require_job_id(job)
        request = self._request_for_stage(stage, job.jd_text or "")
        try:
            response = await self._complete_with_timeout(request)
            result = self._parse_stage_response(stage, response)
        except ScoringParseError as exc:
            await self.error_handler.handle_stage_parse_error(run, stage, job_id, exc)
            return
        except Exception as exc:
            await self.error_handler.handle_stage_runtime_error(run, stage, job_id, exc)
            return
        await self._save_stage_result(stage, job_id, result)
        self._record_stage_success(stage, run, job_id, result)

    def _request_for_stage(self, stage: StageName, jd_text: str) -> LLMRequest:
        if stage == "stage_a":
            return LLMRequest(
                messages=render_stage_a_prompt(
                    jd_text,
                    SKELETON_RESUME,
                    SKELETON_RUBRIC,
                ),
                model=self.settings.llm.stage_a,
            )
        return LLMRequest(
            messages=render_stage_b_prompt(
                jd_text,
                SKELETON_RESUME,
                SKELETON_RUBRIC,
            ),
            model=self.settings.llm.stage_b,
        )

    async def _complete_with_timeout(self, request: LLMRequest) -> LLMResponse:
        return await asyncio.wait_for(
            self.llm.complete(request),
            timeout=self.settings.llm.timeout_s,
        )

    def _parse_stage_response(
        self,
        stage: StageName,
        response: LLMResponse,
    ) -> StageResult:
        if stage == "stage_a":
            return parse_stage_a_response(
                response.content,
                model=response.model,
                prompt_hash=SKELETON_HASH,
                resume_hash=SKELETON_HASH,
                cost_usd=response.cost_usd,
            )
        return parse_stage_b_response(
            response.content,
            model=response.model,
            prompt_hash=SKELETON_HASH,
            resume_hash=SKELETON_HASH,
            cost_usd=response.cost_usd,
        )

    async def _save_stage_result(
        self,
        stage: StageName,
        job_id: str,
        result: StageResult,
    ) -> None:
        if stage == "stage_a":
            if not isinstance(result, StageAResult):
                raise TypeError("stage_a parser returned non-StageAResult")
            await self.store.save_stage_a(job_id, result)
            # Threshold is a service-side policy, not a store concern: below the
            # configured Stage A threshold the job is terminally skipped so it
            # never enters the Stage B queue (plan Decision 1 / Task 1).
            if result.score < self.settings.scoring.stage_a_threshold:
                await self.store.mark_stage_b_skipped(job_id)
            return
        if not isinstance(result, StageBResult):
            raise TypeError("stage_b parser returned non-StageBResult")
        await self.store.save_stage_b(job_id, result)

    def _record_stage_success(
        self,
        stage: StageName,
        run: PipelineRun,
        job_id: str,
        result: StageResult,
    ) -> None:
        run.total_llm_cost_usd += _result_cost_or_zero(result)
        if stage == "stage_a":
            if not isinstance(result, StageAResult):
                raise TypeError("stage_a parser returned non-StageAResult")
            run.stage_a_scored += 1
            self.logger.info("stage_a_scored", job_id=job_id, score=result.score)
            return
        if not isinstance(result, StageBResult):
            raise TypeError("stage_b parser returned non-StageBResult")
        run.stage_b_scored += 1
        self.logger.info(
            "stage_b_scored",
            job_id=job_id,
            score=result.fit_analysis.score,
        )

    def _log_dry_run(self, stage: StageName, jobs: list[JobPosting]) -> None:
        for job in jobs:
            self.logger.info(
                "evaluate_dry_run_job",
                stage=stage,
                job_id=job.id,
                title=job.title,
                company=job.company,
            )


def _require_job_id(job: JobPosting) -> str:
    if job.id is None:
        raise ValueError("evaluated jobs must have a store id")
    return job.id


def _result_cost_or_zero(result: StageResult) -> float:
    return result.cost_usd if result.cost_usd is not None else 0.0


__all__ = ["EvaluateService"]
