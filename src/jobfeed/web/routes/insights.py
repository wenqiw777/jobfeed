"""Insights and attention routes (read-only, thin parse/format shell).

The overview composition lives in ``services/insights.py``; the attention
endpoint makes the same two store calls as the Phase 7 digest footer
(``workflow_attention()`` via the workflow service plus ``needs_attention()``
on the store ops port, both with default thresholds).
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query

from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.services.insights import InsightsService
from jobfeed.services.workflow import WorkflowService
from jobfeed.web.deps import get_insights_service, get_store, get_workflow_service
from jobfeed.web.schemas import (
    AttentionResponse,
    InsightsOverviewResponse,
    attention_response,
    insights_overview_response,
)

_DEFAULT_WINDOW_DAYS = 30
_MAX_WINDOW_DAYS = 365

router = APIRouter()

_Insights = Annotated[InsightsService, Depends(get_insights_service)]
_Workflow = Annotated[WorkflowService, Depends(get_workflow_service)]
_Store = Annotated[JobStore, Depends(get_store)]


@router.get("/insights/overview")
async def insights_overview(
    service: _Insights,
    window: Annotated[int, Query(ge=1, le=_MAX_WINDOW_DAYS)] = _DEFAULT_WINDOW_DAYS,
) -> InsightsOverviewResponse:
    """Aggregate insights: totals, distributions, daily series, app stats.

    Totals and distributions are all-time; the daily series and the
    application stats cover the requested window.

    Args:
        service: Shared insights service from the app state.
        window: Window in days (1..365) for the daily series and app stats.

    Returns:
        Composed overview response.
    """
    bundle = await service.overview(window_days=window)
    return insights_overview_response(bundle)


@router.get("/attention")
async def attention(workflow: _Workflow, store: _Store) -> AttentionResponse:
    """Six-bucket attention report — the digest footer's data over the wire.

    Args:
        workflow: Shared workflow service from the app state.
        store: Shared job store (pipeline-health read).

    Returns:
        Three workflow buckets plus three pipeline-health buckets, both
        with store-default thresholds.
    """
    workflow_buckets = await workflow.attention()
    report = await cast(StoreOpsMixin, store).needs_attention()
    return attention_response(workflow_buckets, report)


__all__ = ["router"]
