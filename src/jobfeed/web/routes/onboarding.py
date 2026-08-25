"""Local-only routes for provider connection onboarding."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_calibration_job import OnboardingCalibrationJobSampler
from jobfeed.onboarding_evaluation_calibration import OnboardingEvaluationCalibrator
from jobfeed.onboarding_plan_usage import (
    CodexPlanUsageReader,
    PlanUsageUnavailable,
)
from jobfeed.onboarding_resume import ResumeOnboardingService
from jobfeed.web.deps import (
    get_onboarding_calibration_job_sampler,
    get_onboarding_evaluation_calibrator,
    get_onboarding_plan_usage_reader,
    get_onboarding_provider_service,
    get_onboarding_resume_service,
)
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.onboarding import (
    ApiErrorResponse,
    CalibrationJobResponse,
    EvaluationCalibrationBody,
    EvaluationCalibrationResponse,
    MeasuredEvaluationCallResponse,
    PlanUsageResponse,
    ProviderConnectionBody,
    ProviderModelsBody,
    ProviderStateResponse,
    provider_state_response,
)

router = APIRouter()
_Service = Annotated[
    OnboardingProviderService, Depends(get_onboarding_provider_service)
]
_Calibrator = Annotated[
    OnboardingEvaluationCalibrator,
    Depends(get_onboarding_evaluation_calibrator),
]
_JobSampler = Annotated[
    OnboardingCalibrationJobSampler,
    Depends(get_onboarding_calibration_job_sampler),
]
_Resume = Annotated[ResumeOnboardingService, Depends(get_onboarding_resume_service)]


@router.get("/onboarding/provider")
async def get_provider_state(service: _Service) -> ProviderStateResponse:
    """Return resumable provider state with secret presence only.

    Args:
        service: Shared provider-onboarding workflow.

    Returns:
        Secret-free provider state.
    """
    return provider_state_response(service.state())


@router.get("/onboarding/plan-usage")
async def get_plan_usage(
    service: _Service,
    reader: Annotated[CodexPlanUsageReader, Depends(get_onboarding_plan_usage_reader)],
) -> PlanUsageResponse:
    """Return live subscription-window usage when the provider exposes it.

    Args:
        service: Shared provider-onboarding workflow.
        reader: Local Codex plan-usage reader.

    Returns:
        Live non-secret usage, or an explicit unavailable response.
    """
    provider = service.state().provider
    if provider != "codex_cli":
        return PlanUsageResponse(
            provider=provider,
            source="unavailable",
            detail="This provider does not expose a live subscription window.",
        )
    try:
        snapshot = await reader.read()
    except PlanUsageUnavailable:
        return PlanUsageResponse(
            provider=provider,
            source="unavailable",
            detail="Live Codex allowance could not be read from this account.",
        )
    return PlanUsageResponse(
        provider=provider,
        source="live",
        plan_name=snapshot.plan_name,
        used_percent=snapshot.used_percent,
        remaining_percent=snapshot.remaining_percent,
        window_minutes=snapshot.window_minutes,
        resets_at=snapshot.resets_at,
        detail="Live Codex allowance from this signed-in account.",
    )


@router.post(
    "/onboarding/evaluation-calibration",
    responses={422: {"model": ApiErrorResponse}},
)
async def calibrate_evaluation(
    body: EvaluationCalibrationBody,
    calibrator: _Calibrator,
) -> EvaluationCalibrationResponse:
    """Run one real unified evaluation and return measured usage.

    Args:
        body: Representative job description to evaluate.
        calibrator: Shared real evaluation calibration workflow.

    Returns:
        Actual tokens, equivalent cost, latency, and plan-window delta.

    Raises:
        ApiError: If provider or profile setup cannot support calibration.
    """
    try:
        result = await calibrator.calibrate(body.job_description)
    except ValueError as exc:
        raise ApiError(422, "calibration_unavailable", str(exc)) from exc
    return EvaluationCalibrationResponse(
        evaluation=MeasuredEvaluationCallResponse(
            model=result.evaluation.model,
            input_tokens=result.evaluation.input_tokens,
            output_tokens=result.evaluation.output_tokens,
            cost_usd=result.evaluation.cost_usd,
            latency_ms=result.evaluation.latency_ms,
        ),
        allowance_before_percent=result.allowance_before_percent,
        allowance_after_percent=result.allowance_after_percent,
    )


@router.get(
    "/onboarding/calibration-job",
    responses={422: {"model": ApiErrorResponse}, 503: {"model": ApiErrorResponse}},
)
async def get_calibration_job(
    sampler: _JobSampler,
    resume: _Resume,
) -> CalibrationJobResponse:
    """Fetch a representative real JD from confirmed Indeed searches.

    Args:
        sampler: Shared confirmed-search sampler.
        resume: Shared résumé/profile onboarding workflow.

    Returns:
        Real posting closest to the bounded sample's mean JD length.

    Raises:
        ApiError: If the profile is unconfirmed or no complete JD is available.
    """
    state = resume.state()
    if not state.is_confirmed or state.profile is None:
        raise ApiError(422, "profile_not_confirmed", "Confirm the job profile first")
    sample = await sampler.sample()
    if sample is None:
        raise ApiError(
            503,
            "calibration_job_unavailable",
            "No complete Indeed job description could be loaded",
        )
    return CalibrationJobResponse(
        id=sample.id,
        title=sample.title,
        company=sample.company,
        url=sample.url,
        jd_text=sample.jd_text,
    )


@router.post(
    "/onboarding/provider/test",
    responses={422: {"model": ApiErrorResponse}},
)
async def test_provider_connection(
    body: ProviderConnectionBody,
    service: _Service,
) -> ProviderStateResponse:
    """Verify provider authentication and return available models.

    Args:
        body: Provider and optional write-only API key.
        service: Shared provider-onboarding workflow.

    Returns:
        Redacted connection result and model catalog.
    """
    api_key = body.api_key.get_secret_value() if body.api_key is not None else None
    state = await service.test_connection(body.provider, api_key=api_key)
    return provider_state_response(state)


@router.put(
    "/onboarding/provider/models",
    responses={422: {"model": ApiErrorResponse}},
)
async def save_provider_models(
    body: ProviderModelsBody,
    service: _Service,
) -> ProviderStateResponse:
    """Persist the provider evaluation model in compatibility fields.

    Args:
        body: Provider and selected model ids.
        service: Shared provider-onboarding workflow.

    Returns:
        Updated provider draft.

    Raises:
        ApiError: If the provider or models do not match verified state.
    """
    try:
        state = service.save_models(
            body.provider,
            body.quick_model,
            body.detailed_model,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_model_selection", str(exc)) from exc
    return provider_state_response(state)


__all__ = ["router"]
