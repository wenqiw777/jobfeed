"""Shared types for the provider-connection onboarding milestone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderName = Literal[
    "openai_api",
    "anthropic_api",
    "azure_openai",
    "codex_cli",
    "claude_cli",
    "amazon_bedrock",
]

API_PROVIDERS = frozenset({"openai_api", "anthropic_api", "azure_openai"})


@dataclass(frozen=True, kw_only=True)
class DeploymentPricing:
    """User-confirmed Azure price profile for one deployment alias."""

    deployment: str
    base_model: str
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None


@dataclass(frozen=True, kw_only=True)
class ModelPriceReference:
    """Vendored reference rates offered as editable Azure price defaults."""

    base_model: str
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None


@dataclass(frozen=True, kw_only=True)
class ProviderModel:
    """One model available through a verified provider connection."""

    id: str
    label: str
    kind: Literal["model", "foundation_model", "inference_profile"] = "model"
    pricing_model: str | None = None


@dataclass(frozen=True, kw_only=True)
class ConnectionResult:
    """Redacted outcome from a provider authentication and discovery check."""

    provider: ProviderName
    connected: bool
    detail: str
    models: tuple[ProviderModel, ...] = ()
    region: str | None = None
    profile: str | None = None
    endpoint: str | None = None
    pricing_catalog: tuple[ModelPriceReference, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ProviderOnboardingState:
    """Resumable, secret-free provider onboarding state."""

    provider: ProviderName | None = None
    connected: bool = False
    detail: str | None = None
    models: tuple[ProviderModel, ...] = ()
    has_secret: bool = False
    quick_model: str | None = None
    detailed_model: str | None = None
    region: str | None = None
    profile: str | None = None
    endpoint: str | None = None
    deployment_pricing: tuple[DeploymentPricing, ...] = ()
    pricing_catalog: tuple[ModelPriceReference, ...] = ()


__all__ = [
    "API_PROVIDERS",
    "ConnectionResult",
    "DeploymentPricing",
    "ModelPriceReference",
    "ProviderModel",
    "ProviderName",
    "ProviderOnboardingState",
]
