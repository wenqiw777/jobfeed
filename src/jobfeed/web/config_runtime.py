"""Apply saved GUI configuration to future web work."""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI

from jobfeed.cli import AppContext
from jobfeed.cli._probe import build_probe_company
from jobfeed.config import Settings
from jobfeed.services.jobs_view import JobsViewService, JobsViewStore


def apply_runtime_settings(
    app: FastAPI, context: AppContext, settings: Settings
) -> None:
    """Apply saved settings to work scheduled after the save completes.

    The SQLite store remains open on its original path. Run-manager factories
    and source resolution read ``context`` lazily, so replacing the settings
    there updates future scan/evaluate runs while in-flight work keeps its
    immutable build-time configuration.

    Args:
        app: Running FastAPI application.
        context: Mutable composition-root context captured by run factories.
        settings: Newly validated complete settings.
    """
    context["settings"] = settings
    app.state.probe_company = context["probe_company"] = build_probe_company(settings)
    app.state.jobs_view_service = JobsViewService(
        store=cast(JobsViewStore, context["store"]),
        hard_filters=settings.hard_filters.to_domain(),
    )


__all__ = ["apply_runtime_settings"]
