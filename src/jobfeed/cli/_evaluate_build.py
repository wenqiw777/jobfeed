"""Composition root for the evaluate command: build + run EvaluateService.

Lives apart from cli/evaluate.py so the command module stays a thin Click
shell. Delegates to the shared ``cli/_evaluate_factory`` for the actual
EvaluateService construction -- the factory is Click-free so the web layer
can reuse it. This module converts Click-specific errors and holds the
CLI-only ``_EvalParams`` dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass

import click

from jobfeed.cli import AppContext
from jobfeed.cli._evaluate_factory import EvalBuildParams, build_evaluate_service
from jobfeed.domain.errors import ResumeNotConfiguredError
from jobfeed.domain.models import PipelineRun


@dataclass(frozen=True, kw_only=True)
class _EvalParams:
    """Typed container for evaluate CLI flags."""

    stage: str
    corpus: str
    limit: int | None
    max_days: int | None
    parallel: int | None
    threshold: int | None
    dry_run: bool


async def build_and_run(
    app: AppContext,
    params: _EvalParams,
) -> PipelineRun:
    """Construct dependencies and run evaluation.

    Delegates to the shared factory for service construction and wraps
    non-Click errors into ClickException for CLI callers.

    Args:
        app: Initialized application context.
        params: Typed evaluate CLI flags.

    Returns:
        Pipeline run with counters.

    Raises:
        click.ClickException: If the master resume file is missing on a
            non-dry run.
    """
    build_params = EvalBuildParams(
        stage=params.stage,
        corpus=params.corpus,
        limit=params.limit,
        max_days=params.max_days,
        parallel=params.parallel,
        threshold=params.threshold,
        dry_run=params.dry_run,
    )
    try:
        service = build_evaluate_service(app, build_params)
    except (FileNotFoundError, ResumeNotConfiguredError) as exc:
        raise click.ClickException(str(exc)) from exc
    return await service.run(
        stage=params.stage,
        corpus=params.corpus,
        limit=params.limit,
        max_days=params.max_days,
        dry_run=params.dry_run,
    )
