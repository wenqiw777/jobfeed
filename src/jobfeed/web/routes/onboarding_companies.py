"""Routes for profile-derived onboarding company recommendations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from jobfeed.onboarding_companies import (
    CompanyCatalogState,
    CompanyRecommendationState,
    OnboardingCompanyService,
)
from jobfeed.web.deps import (
    CompanyCatalogLoader,
    get_onboarding_company_catalog,
    get_onboarding_company_service,
)
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.onboarding import ApiErrorResponse

router = APIRouter()
_Service = Annotated[
    OnboardingCompanyService,
    Depends(get_onboarding_company_service),
]
_Catalog = Annotated[
    CompanyCatalogLoader,
    Depends(get_onboarding_company_catalog),
]


@router.post(
    "/onboarding/companies/recommend",
    responses={422: {"model": ApiErrorResponse}},
)
async def recommend_onboarding_companies(
    service: _Service,
    refresh: Annotated[bool, Query()] = False,
) -> CompanyRecommendationState:
    """Generate or resume company candidates for the confirmed profile.

    Args:
        service: Shared company recommendation workflow.
        refresh: Whether to bypass matching persisted suggestions.

    Returns:
        Profile-bound company recommendation state.

    Raises:
        ApiError: If provider or profile setup is incomplete.
    """
    try:
        return await service.recommend(refresh=refresh)
    except ValueError as exc:
        raise ApiError(422, "company_recommendation_failed", str(exc)) from exc


@router.get(
    "/onboarding/companies/catalog",
    responses={503: {"model": ApiErrorResponse}},
)
async def get_onboarding_company_catalog_entries(
    load_catalog: _Catalog,
) -> CompanyCatalogState:
    """Load broad canonical company/vendor rows from public job lists.

    Args:
        load_catalog: Injected public ATS catalog loader.

    Returns:
        Deduplicated company/vendor catalog and per-source counts.

    Raises:
        ApiError: If every public catalog fails to load.
    """
    try:
        return await load_catalog()
    except ValueError as exc:
        raise ApiError(503, "company_catalog_unavailable", str(exc)) from exc


__all__ = ["router"]
