"""Shared types for the provider-connection onboarding milestone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderName = Literal[
    "openai_api",
    "anthropic_api",
    "codex_cli",
    "claude_cli",
]

API_PROVIDERS = frozenset({"openai_api", "anthropic_api"})


@dataclass(frozen=True, kw_only=True)
class ProviderModel:
    """One model available through a verified provider connection."""

    id: str
    label: str


@dataclass(frozen=True, kw_only=True)
class ConnectionResult:
    """Redacted outcome from a provider authentication and discovery check."""

    provider: ProviderName
    connected: bool
    detail: str
    models: tuple[ProviderModel, ...] = ()


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


__all__ = [
    "API_PROVIDERS",
    "ConnectionResult",
    "ProviderModel",
    "ProviderName",
    "ProviderOnboardingState",
]
