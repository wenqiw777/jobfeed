"""Wire models for provider onboarding without secret-value responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from jobfeed.onboarding_types import ProviderName, ProviderOnboardingState


class ProviderConnectionBody(BaseModel):
    """Provider selection plus an optional write-only API key."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    api_key: SecretStr | None = None


class ProviderModelsBody(BaseModel):
    """Quick and Detailed model selections from a verified catalog."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    quick_model: str
    detailed_model: str


class ProviderModelOut(BaseModel):
    """One provider model safe to expose to the browser."""

    id: str
    label: str


class ProviderStateResponse(BaseModel):
    """Resumable provider state containing no raw API key."""

    provider: ProviderName | None = None
    connected: bool
    detail: str | None = None
    models: list[ProviderModelOut]
    has_secret: bool
    quick_model: str | None = None
    detailed_model: str | None = None


class PlanUsageResponse(BaseModel):
    """Live provider-plan allowance safe to show during onboarding."""

    provider: ProviderName | None
    source: Literal["live", "unavailable"]
    plan_name: str | None = None
    used_percent: int | None = None
    remaining_percent: int | None = None
    window_minutes: int | None = None
    resets_at: int | None = None
    detail: str


class EvaluationCalibrationBody(BaseModel):
    """One representative JD used for a real two-stage calibration."""

    model_config = ConfigDict(extra="forbid")

    job_description: str = Field(min_length=100, max_length=30_000)


class CalibrationJobResponse(BaseModel):
    """One mean-length real posting from the confirmed Indeed searches."""

    id: str
    title: str
    company: str
    url: str
    jd_text: str


class MeasuredEvaluationCallResponse(BaseModel):
    """Measured usage for one real evaluation-stage model call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class EvaluationCalibrationResponse(BaseModel):
    """Measured Quick + Detailed usage and subscription-meter delta."""

    quick: MeasuredEvaluationCallResponse
    detailed: MeasuredEvaluationCallResponse
    allowance_before_percent: int | None
    allowance_after_percent: int | None
    allowance_resolution_percent: int = 1


class ApiErrorDetail(BaseModel):
    """Shared machine-readable API error details."""

    code: str
    message: str
    request_id: str


class ApiErrorResponse(BaseModel):
    """Shared JSON error envelope returned by onboarding mutations."""

    error: ApiErrorDetail


def provider_state_response(state: ProviderOnboardingState) -> ProviderStateResponse:
    """Convert internal provider state to its redacted wire shape.

    Args:
        state: Secret-free internal provider state.

    Returns:
        Validated response model safe for the browser.
    """
    return ProviderStateResponse(
        provider=state.provider,
        connected=state.connected,
        detail=state.detail,
        models=[
            ProviderModelOut(id=model.id, label=model.label) for model in state.models
        ],
        has_secret=state.has_secret,
        quick_model=state.quick_model,
        detailed_model=state.detailed_model,
    )


__all__ = [
    "ApiErrorResponse",
    "CalibrationJobResponse",
    "EvaluationCalibrationBody",
    "EvaluationCalibrationResponse",
    "MeasuredEvaluationCallResponse",
    "PlanUsageResponse",
    "ProviderConnectionBody",
    "ProviderModelsBody",
    "ProviderStateResponse",
    "provider_state_response",
]
