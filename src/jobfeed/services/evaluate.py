"""Single-pass objective job evaluation service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from jobfeed.domain.models import PipelineRun
from jobfeed.observability import JobfeedLogger, bind_run_id, get_tracer
from jobfeed.ports.run_leases import RunLeaseStore
from jobfeed.services._evaluate_budget import EvaluateBudgetGate
from jobfeed.services._evaluate_claims import validate_evaluate_stage
from jobfeed.services._evaluate_dryrun import DryRunRequest, build_dry_run_preview
from jobfeed.services._evaluate_helpers import run_auto_decay
from jobfeed.services._evaluate_unified import run_unified_evaluation
from jobfeed.services._timing import StepTimer, get_perf_store
from jobfeed.services.evaluate_types import EvaluateDependencies, EvaluateRuntimeConfig
from jobfeed.services.run_orchestration import RunLeaseOrchestrator, RunLeaseSession


class EvaluateService:
    """Evaluate each claimed job once and persist one canonical result."""

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

    async def run(  # noqa: PLR0913 - public run filters
        self,
        *,
        stage: str = "unified",
        corpus: str = "unrated",
        limit: int | None = None,
        max_days: int | None = None,
        dry_run: bool = False,
        on_progress: Callable[[PipelineRun], None] | None = None,
        run: PipelineRun | None = None,
        lease_session: RunLeaseSession | None = None,
    ) -> PipelineRun:
        """Evaluate the requested corpus with one model call per job.

        The legacy stage selector remains accepted at API boundaries during the
        client migration, but every value routes to this one evaluator.
        """
        validate_evaluate_stage(stage)
        cap = self._config.default_eval_limit if limit is None else limit
        if dry_run:
            return await self._run_dry(
                stage=stage,
                corpus=corpus,
                limit=cap,
                max_days=max_days,
                on_progress=on_progress,
                run=run,
                lease_session=lease_session,
            )
        if run is not None:
            raise ValueError("persisted runs require a lease session, not a bare run")
        if lease_session is None:
            result = await self._run_orchestrator.run(
                "evaluate",
                "evaluate",
                lambda session: self._run_leased(
                    session,
                    corpus=corpus,
                    limit=cap,
                    max_days=max_days,
                    on_progress=on_progress,
                ),
            )
            if on_progress is not None:
                on_progress(result)
            return result
        await self._run_leased(
            lease_session,
            corpus=corpus,
            limit=cap,
            max_days=max_days,
            on_progress=on_progress,
        )
        return lease_session.run

    async def _run_dry(  # noqa: PLR0913 - mirrors public filters
        self,
        *,
        stage: str,
        corpus: str,
        limit: int,
        max_days: int | None,
        on_progress: Callable[[PipelineRun], None] | None,
        run: PipelineRun | None,
        lease_session: RunLeaseSession | None,
    ) -> PipelineRun:
        if lease_session is not None:
            raise ValueError("dry-run must not receive a lease session")
        preview_run = run or self._run_orchestrator.new_unpersisted_run("evaluate")
        bind_run_id(preview_run.run_id)
        request = DryRunRequest(self._logger, stage, corpus, limit, max_days)
        await build_dry_run_preview(self._deps, self._config, preview_run, request)
        self._run_orchestrator.finish_unpersisted(preview_run, "succeeded")
        if on_progress is not None:
            on_progress(preview_run)
        return preview_run

    async def _run_leased(
        self,
        lease_session: RunLeaseSession,
        *,
        corpus: str,
        limit: int,
        max_days: int | None,
        on_progress: Callable[[PipelineRun], None] | None,
    ) -> None:
        run = lease_session.run
        bind_run_id(run.run_id)
        self._on_progress = on_progress
        run.evaluate_stage = "unified"
        run.progress_stage = "preparing"
        self._emit_progress(run)
        lease_session.ensure_active()
        await run_auto_decay(self._deps, self._config, self._logger)
        lease_session.ensure_active()
        async with self._st(run.run_id, "stage", "evaluation"):
            await run_unified_evaluation(
                self,
                run,
                corpus=corpus,
                limit=limit,
                max_days=max_days,
                lease_session=lease_session,
            )
        run.progress_stage = "finalizing"
        self._emit_progress(run)

    def _emit_progress(self, run: PipelineRun) -> None:
        run.progress_updated_at = datetime.now(UTC)
        if self._on_progress is not None:
            self._on_progress(run)

    def _st(self, run_id: str, step_type: str, step_name: str) -> StepTimer:
        return StepTimer(self._perf, run_id, step_type, step_name, self._tracer)


__all__ = ["EvaluateService"]
