"""Local-only routes for résumé upload and job-profile confirmation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from jobfeed.onboarding_resume import ResumeOnboardingService
from jobfeed.onboarding_resume_types import JobProfile
from jobfeed.web.deps import get_onboarding_resume_service
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.onboarding import ApiErrorResponse
from jobfeed.web.schemas.onboarding_resume import (
    ResumeStateResponse,
    resume_state_response,
)

router = APIRouter()
_Service = Annotated[ResumeOnboardingService, Depends(get_onboarding_resume_service)]


@router.get("/onboarding/resume")
async def get_resume_state(service: _Service) -> ResumeStateResponse:
    """Return the resumable path-free résumé and profile draft.

    Args:
        service: Shared résumé onboarding workflow.

    Returns:
        Browser-safe résumé and profile state.
    """
    return resume_state_response(service.state())


@router.post(
    "/onboarding/resume",
    responses={422: {"model": ApiErrorResponse}},
)
async def upload_resume(
    service: _Service,
    file: Annotated[UploadFile, File()],
) -> ResumeStateResponse:
    """Validate, extract, and save one original résumé upload.

    Args:
        service: Shared résumé onboarding workflow.
        file: Supported original résumé file.

    Returns:
        Extracted preview without a local path.

    Raises:
        ApiError: If the upload cannot be validated or extracted.
    """
    try:
        state = service.upload(file.filename or "resume", await file.read())
    except ValueError as exc:
        raise ApiError(422, "invalid_resume", str(exc)) from exc
    return resume_state_response(state)


@router.post(
    "/onboarding/resume/analyze",
    responses={422: {"model": ApiErrorResponse}},
)
async def analyze_resume(service: _Service) -> ResumeStateResponse:
    """Analyze the uploaded résumé with the selected Detailed model.

    Args:
        service: Shared résumé onboarding workflow.

    Returns:
        Draft containing the validated profile suggestion.

    Raises:
        ApiError: If upload or provider setup is incomplete or analysis fails.
    """
    try:
        state = await service.analyze()
    except ValueError as exc:
        raise ApiError(422, "resume_analysis_failed", str(exc)) from exc
    return resume_state_response(state)


@router.put(
    "/onboarding/profile",
    responses={422: {"model": ApiErrorResponse}},
)
async def confirm_profile(
    body: JobProfile,
    service: _Service,
) -> ResumeStateResponse:
    """Persist the user's edited profile as explicitly confirmed.

    Args:
        body: Complete edited profile.
        service: Shared résumé onboarding workflow.

    Returns:
        Confirmed resumable draft.

    Raises:
        ApiError: If no résumé draft exists.
    """
    try:
        state = service.confirm(body)
    except ValueError as exc:
        raise ApiError(422, "invalid_profile", str(exc)) from exc
    return resume_state_response(state)


__all__ = ["router"]
