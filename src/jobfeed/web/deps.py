"""Accessors for the per-process dependency graph on the web app state."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from jobfeed.cli import AppContext
from jobfeed.cli._probe import ProbeVendorFn
from jobfeed.config_editor import ConfigurationEditor
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
    "ProbeVendorFn",
    "get_application_service",
    "get_configuration_editor",
    "get_context",
    "get_insights_service",
    "get_jobs_view_service",
    "get_performance_service",
    "get_probe_company",
    "get_run_manager",
    "get_store",
    "get_workflow_service",
]
