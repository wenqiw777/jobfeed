"""Shared PipelineRun construction helpers for application services."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from jobfeed.domain.models import PipelineRun


def start_pipeline_run(source: str) -> PipelineRun:
    """Create a PipelineRun with a fresh identity and UTC start time.

    Args:
        source: Operation or source label for the run.

    Returns:
        New pipeline run counter object.
    """
    return PipelineRun(
        run_id=str(uuid4()),
        started_at=datetime.now(UTC),
        source=source,
    )


__all__ = ["start_pipeline_run"]
