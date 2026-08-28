"""Wire models for provider onboarding without secret-value responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from jobfeed.onboarding_types import (
    DeploymentPricing,
    ProviderName,
    ProviderOnboardingState,
)


class DeploymentPricingBody(BaseModel):
    """Confirmed Azure pricing for one selected deployment alias."""

    model_config = ConfigDict(extra="forbid")

    deployment: str = Field(min_length=1)
    base_model: str = Field(min_length=1)
    input_usd_per_million: float = Field(ge=0)
    output_usd_per_million: float = Field(ge=0)
    cached_input_usd_per_million: float | None = Field(default=None, ge=0)

    def to_domain(self) -> DeploymentPricing:
        """Convert the validated wire value to the onboarding domain type.

        Returns:
            Confirmed deployment pricing for the onboarding domain.
        """
        return DeploymentPricing(**self.model_dump())


class ModelPriceReferenceOut(BaseModel):
    """Editable reference rates safe to offer in Azure onboarding."""

    base_model: str
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None


class ProviderConnectionBody(BaseModel):
    """Provider selection plus an optional write-only API key."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    api_key: SecretStr | None = None
    region: str | None = None
    profile: str | None = None
    endpoint: str | None = None


class ProviderModelsBody(BaseModel):
    """Quick and Detailed model selections from a verified catalog."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    quick_model: str
    detailed_model: str
    deployment_pricing: list[DeploymentPricingBody] = Field(default_factory=list)


class ProviderModelOut(BaseModel):
    """One provider model safe to expose to the browser."""

    id: str
    label: str
    kind: Literal["model", "foundation_model", "inference_profile"] = "model"


class ProviderStateResponse(BaseModel):
    """Resumable provider state containing no raw API key."""

    provider: ProviderName | None = None
    connected: bool
    detail: str | None = None
    models: list[ProviderModelOut]
    has_secret: bool
    quick_model: str | None = None
    detailed_model: str | None = None
    region: str | None = None
    profile: str | None = None
    endpoint: str | None = None
    deployment_pricing: list[DeploymentPricingBody] = Field(default_factory=list)
    pricing_catalog: list[ModelPriceReferenceOut] = Field(default_factory=list)


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
            ProviderModelOut(id=model.id, label=model.label, kind=model.kind)
            for model in state.models
        ],
        has_secret=state.has_secret,
        quick_model=state.quick_model,
        detailed_model=state.detailed_model,
        region=state.region,
        profile=state.profile,
        endpoint=state.endpoint,
        deployment_pricing=[
            DeploymentPricingBody.model_validate(price.__dict__)
            for price in state.deployment_pricing
        ],
        pricing_catalog=[
            ModelPriceReferenceOut.model_validate(price.__dict__)
            for price in state.pricing_catalog
        ],
    )


__all__ = [
    "ApiErrorResponse",
    "CalibrationJobResponse",
    "DeploymentPricingBody",
    "EvaluationCalibrationBody",
    "EvaluationCalibrationResponse",
    "MeasuredEvaluationCallResponse",
    "ModelPriceReferenceOut",
    "PlanUsageResponse",
    "ProviderConnectionBody",
    "ProviderModelsBody",
    "ProviderStateResponse",
    "provider_state_response",
]
