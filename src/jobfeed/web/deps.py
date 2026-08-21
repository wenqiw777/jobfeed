"""Accessors for the per-process dependency graph on the web app state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import Request

from jobfeed.cli import AppContext
from jobfeed.cli._probe import ProbeVendorFn
from jobfeed.config_editor import ConfigurationEditor
from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_calibration_job import OnboardingCalibrationJobSampler
from jobfeed.onboarding_companies import CompanyCatalogState, OnboardingCompanyService
from jobfeed.onboarding_evaluation_calibration import OnboardingEvaluationCalibrator
from jobfeed.onboarding_plan_usage import CodexPlanUsageReader
from jobfeed.onboarding_resume import ResumeOnboardingService
from jobfeed.onboarding_searches import OnboardingSearchService
from jobfeed.personal_ml_learning import PersonalMLLearningService
from jobfeed.ports.store import JobStore
from jobfeed.services.application import ApplicationService
from jobfeed.services.insights import InsightsService
from jobfeed.services.jobs_view import JobsViewService
from jobfeed.services.performance import PerformanceService
from jobfeed.services.run_manager import RunManager
from jobfeed.services.workflow import WorkflowService


def get_context(request: Request) -> AppContext:
    """Return the per-process dependency graph stored on ``app.state``.

    The context is assembled once by the web app factory and shared by every
    request; handlers must never build stores or services themselves.

    Args:
        request: Current request.

    Returns:
        Shared application context.
    """
    return cast(AppContext, request.app.state.context)


def get_store(request: Request) -> JobStore:
    """Return the shared job store.

    Args:
        request: Current request.

    Returns:
        Job store whose connection is owned by the app lifespan.
    """
    return get_context(request)["store"]


def get_configuration_editor(request: Request) -> ConfigurationEditor:
    """Return the project-local GUI configuration editor.

    Args:
        request: Current request.

    Returns:
        Shared editor that persists and applies validated settings.
    """
    return cast(ConfigurationEditor, request.app.state.configuration_editor)


def get_personal_ml_service(request: Request) -> PersonalMLLearningService:
    """Return the shared personal relevance-learning service.
    Args: Current request carrying application state.
    Returns: Shared personal relevance-learning service.
    """
    return cast(PersonalMLLearningService, request.app.state.personal_ml_service)


def get_onboarding_provider_service(request: Request) -> OnboardingProviderService:
    """Return the provider-onboarding workflow assembled by the app factory.

    Args:
        request: Current request carrying application state.

    Returns:
        Shared provider-onboarding workflow.
    """
    return cast(
        OnboardingProviderService,
        request.app.state.onboarding_provider_service,
    )


def get_onboarding_plan_usage_reader(request: Request) -> CodexPlanUsageReader:
    """Return the local provider-plan usage reader.
    Args: Current request carrying application state.
    Returns: Shared local Codex plan-usage reader.
    """
    return cast(
        CodexPlanUsageReader,
        request.app.state.onboarding_plan_usage_reader,
    )


def get_onboarding_evaluation_calibrator(
    request: Request,
) -> OnboardingEvaluationCalibrator:
    """Return the real two-stage evaluation calibration workflow.
    Args: Current request carrying application state.
    Returns: Shared evaluation calibration workflow.
    """
    return cast(
        OnboardingEvaluationCalibrator,
        request.app.state.onboarding_evaluation_calibrator,
    )


def get_onboarding_calibration_job_sampler(
    request: Request,
) -> OnboardingCalibrationJobSampler:
    """Return the confirmed-search sampler used by onboarding calibration.
    Args: Current request carrying application state.
    Returns: Shared representative-JD sampler.
    """
    return cast(
        OnboardingCalibrationJobSampler,
        request.app.state.onboarding_calibration_job_sampler,
    )


def get_onboarding_resume_service(request: Request) -> ResumeOnboardingService:
    """Return the résumé/profile onboarding workflow assembled by the app.

    Args:
        request: Current request carrying application state.

    Returns:
        Shared résumé/profile onboarding workflow.
    """
    return cast(
        ResumeOnboardingService,
        request.app.state.onboarding_resume_service,
    )


def get_onboarding_search_service(request: Request) -> OnboardingSearchService:
    """Return the confirmed-profile search onboarding workflow.

    Args:
        request: Current request carrying application state.

    Returns:
        Shared resumable search-selection workflow.
    """
    return cast(
        OnboardingSearchService,
        request.app.state.onboarding_search_service,
    )


def get_onboarding_company_service(request: Request) -> OnboardingCompanyService:
    """Return the profile-derived company recommendation workflow.
    Args: Current request carrying application state.
    Returns: Shared company recommendation workflow.
    """
    return cast(
        OnboardingCompanyService,
        request.app.state.onboarding_company_service,
    )


CompanyCatalogLoader = Callable[[], Awaitable[CompanyCatalogState]]


def get_onboarding_company_catalog(request: Request) -> CompanyCatalogLoader:
    """Return the injected public ATS company-catalog loader.
    Args: Current request carrying application state.
    Returns: Async public catalog loader.
    """
    return cast(
        CompanyCatalogLoader,
        request.app.state.onboarding_company_catalog,
    )


def get_jobs_view_service(request: Request) -> JobsViewService:
    """Return the per-process jobs view service built by the app factory.

    Args:
        request: Current request.

    Returns:
        Shared jobs view service.
    """
    return cast(JobsViewService, request.app.state.jobs_view_service)


def get_workflow_service(request: Request) -> WorkflowService:
    """Return the per-process workflow service built by the app factory.

    Args:
        request: Current request.

    Returns:
        Shared workflow service.
    """
    return cast(WorkflowService, request.app.state.workflow_service)


def get_application_service(request: Request) -> ApplicationService:
    """Return the per-process application service built by the app factory.

    Args:
        request: Current request.

    Returns:
        Shared application service.
    """
    return cast(ApplicationService, request.app.state.application_service)


def get_probe_company(request: Request) -> ProbeVendorFn:
    """Return the per-slug ATS vendor probe wired by the cli assembly.

    The callable is adapter-backed but injected through the context, so web
    modules never import ``jobfeed.adapters`` (architecture boundary).

    Args:
        request: Current request.

    Returns:
        Async probe mapping a slug to its vendor name (None on a miss).

    Raises:
        RuntimeError: If the app was assembled without a probe callable —
            failing loudly here beats a "'NoneType' is not callable" deep
            inside the probe route.
    """
    probe = getattr(request.app.state, "probe_company", None)
    if probe is None:
        raise RuntimeError(
            "web app context missing probe_company; create_app assembles it, "
            "and fake contexts that exercise probe routes must supply a stub"
        )
    return cast(ProbeVendorFn, probe)


def get_insights_service(request: Request) -> InsightsService:
    """Return the per-process insights service built by the app factory.

    Args:
        request: Current request.

    Returns:
        Shared insights service.
    """
    return cast(InsightsService, request.app.state.insights_service)


def get_run_manager(request: Request) -> RunManager:
    """Return the per-process run manager built by the app factory.

    Args:
        request: Current request.

    Returns:
        Shared run manager for trigger and progress operations.
    """
    return cast(RunManager, request.app.state.run_manager)


def get_performance_service(request: Request) -> PerformanceService:
    """Return the per-process performance service built by the app factory.

    Args:
        request: Current request.

    Returns:
        Shared performance service.
    """
    return cast(PerformanceService, request.app.state.performance_service)


__all__ = [
    "CompanyCatalogLoader",
    "ProbeVendorFn",
    "get_application_service",
    "get_configuration_editor",
    "get_context",
    "get_insights_service",
    "get_jobs_view_service",
    "get_onboarding_company_catalog",
    "get_onboarding_company_service",
    "get_onboarding_plan_usage_reader",
    "get_onboarding_provider_service",
    "get_onboarding_resume_service",
    "get_performance_service",
    "get_probe_company",
    "get_run_manager",
    "get_store",
    "get_workflow_service",
]
