"""Routes for generated and user-edited onboarding searches."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from jobfeed.onboarding_searches import (
    OnboardingSearchService,
    SearchDraftState,
    SearchSuggestion,
)
from jobfeed.web.deps import get_onboarding_search_service
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.onboarding import ApiErrorResponse

router = APIRouter()
_Service = Annotated[
    OnboardingSearchService,
    Depends(get_onboarding_search_service),
]


class SearchSelectionBody(BaseModel):
    """Complete editable search selection submitted by the browser."""

    model_config = ConfigDict(extra="forbid")

    searches: list[SearchSuggestion]


@router.get(
    "/onboarding/searches",
    responses={422: {"model": ApiErrorResponse}},
)
async def get_onboarding_searches(service: _Service) -> SearchDraftState:
    """Generate or resume searches for the confirmed profile.

    Args:
        service: Shared search-selection workflow.

    Returns:
        Profile-bound editable search suggestions.

    Raises:
        ApiError: If the profile has not been explicitly confirmed.
    """
    try:
        return service.state()
    except ValueError as exc:
        raise ApiError(422, "profile_not_confirmed", str(exc)) from exc


@router.put(
    "/onboarding/searches",
    responses={422: {"model": ApiErrorResponse}},
)
async def put_onboarding_searches(
    body: SearchSelectionBody,
    service: _Service,
) -> SearchDraftState:
    """Persist edited, added, and deselected search rows.

    Args:
        body: Complete validated search selection.
        service: Shared search-selection workflow.

    Returns:
        Persisted profile-bound search draft.

    Raises:
        ApiError: If the profile has not been explicitly confirmed.
    """
    try:
        return service.save(body.searches)
    except ValueError as exc:
        raise ApiError(422, "invalid_search_selection", str(exc)) from exc


__all__ = ["router"]
