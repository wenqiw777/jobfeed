"""Compose onboarding, personal-ML, and post-scan web capabilities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI

from jobfeed.cli import AppContext
from jobfeed.cli.bootstrap import load_company_catalog
from jobfeed.cli.enrich import run_guest_enrich_pass
from jobfeed.domain.models import PipelineRun
from jobfeed.onboarding_calibration_job import (
    OnboardingCalibrationJobSampler,
    fetch_indeed_sample,
)
from jobfeed.onboarding_evaluation_calibration import OnboardingEvaluationCalibrator
from jobfeed.onboarding_plan_usage import CodexPlanUsageReader
from jobfeed.onboarding_web import build_onboarding_services
from jobfeed.personal_ml_learning import (
    PersonalMLLearningService,
    PersonalMLObservationStore,
)
from jobfeed.services.enrich import EnrichProgress
from jobfeed.services.scan import SourceSpec
from jobfeed.web.routes.onboarding import router as onboarding_router
from jobfeed.web.routes.onboarding_companies import (
    router as onboarding_companies_router,
)
from jobfeed.web.routes.onboarding_resume import router as onboarding_resume_router
from jobfeed.web.routes.onboarding_searches import router as onboarding_searches_router
from jobfeed.web.routes.personal_ml import router as personal_ml_router

_PostScanHook = Callable[
    [PipelineRun, list[SourceSpec], Callable[[PipelineRun], None]], Awaitable[None]
]


def _configure_onboarding(
    app: FastAPI,
    context: AppContext,
    project_root: Path,
    logger: Any,
    store: object,
) -> None:
    """Attach onboarding and personal-ML services to application state."""
    app.state.personal_ml_service = PersonalMLLearningService(
        cast(PersonalMLObservationStore, store)
    )
    provider, resume, searches, companies = build_onboarding_services(
        project_root, logger
    )
    app.state.onboarding_provider_service = provider
    app.state.onboarding_resume_service = resume
    app.state.onboarding_search_service = searches
    app.state.onboarding_company_service = companies
    app.state.onboarding_company_catalog = load_company_catalog
    usage = CodexPlanUsageReader()
    app.state.onboarding_plan_usage_reader = usage
    app.state.onboarding_evaluation_calibrator = OnboardingEvaluationCalibrator(
        provider_state=provider.state,
        resume_state=resume.state,
        plan_usage_reader=usage,
        logger=logger,
    )
    app.state.onboarding_calibration_job_sampler = OnboardingCalibrationJobSampler(
        search_state=searches.state,
        fetch_indeed=partial(
            fetch_indeed_sample,
            config=context["settings"].sources.indeed,
            logger=logger,
        ),
    )


def _include_onboarding_routers(app: FastAPI) -> None:
    """Mount onboarding and personal-ML routes under the API prefix."""
    for router in (
        onboarding_router,
        onboarding_resume_router,
        onboarding_searches_router,
        onboarding_companies_router,
        personal_ml_router,
    ):
        app.include_router(router, prefix="/api")


def _make_post_scan_hook(context: AppContext) -> _PostScanHook:
    """Automatically fetch LinkedIn Guest JDs after web discovery."""

    async def _post_scan(
        run: PipelineRun,
        specs: list[SourceSpec],
        on_progress: Callable[[PipelineRun], None],
    ) -> None:
        if all(name != "linkedin_guest" for name, _, _ in specs):
            return
        config = context["settings"].sources.linkedin_guest
        if not config.enrich_after_scan:
            return
        logger = context["logger"]

        def _report(progress: EnrichProgress) -> None:
            run.scan_source = progress.platform
            run.scan_phase = "enriching_job_descriptions"
            run.scan_total = progress.total
            run.scan_processed = progress.processed
            run.scan_current_job_id = progress.current_job_id
            run.progress_updated_at = datetime.now(UTC)
            on_progress(run)

        try:
            summary = await run_guest_enrich_pass(
                context,
                config,
                batch_limit=len(run.scan_inserted_job_ids),
                job_ids=run.scan_inserted_job_ids,
                on_progress=_report,
            )
        except Exception as exc:
            logger.error("web_scan_guest_enrich_failed", error=str(exc))
            return
        logger.info(
            "web_scan_guest_enrich_completed",
            enriched=summary.enriched,
            closed=summary.closed,
            blocked=summary.blocked,
            skipped=summary.skipped,
            stopped_early=summary.stopped_early,
        )

    return _post_scan
