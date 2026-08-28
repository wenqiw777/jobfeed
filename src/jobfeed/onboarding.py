"""Application workflow for provider connection and model onboarding."""

from __future__ import annotations

import asyncio
from typing import Protocol

from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_state import OnboardingDraftStore
from jobfeed.onboarding_types import (
    API_PROVIDERS,
    ConnectionResult,
    DeploymentPricing,
    ProviderName,
    ProviderOnboardingState,
)


class ProviderCheck(Protocol):
    """Capability required to verify one provider connection."""

    async def check(
        self,
        provider: ProviderName,
        *,
        api_key: str | None = None,
        region: str | None = None,
        profile: str | None = None,
        endpoint: str | None = None,
    ) -> ConnectionResult:
        """Return redacted connection evidence and available models.

        Args:
            provider: Provider to verify.
            api_key: Optional API credential for official API providers.

        Returns:
            Redacted connection result and model catalog.
        """
        ...


class OnboardingProviderService:
    """Coordinate redacted provider checks, secrets, and resumable draft state."""

    def __init__(
        self,
        *,
        checker: ProviderCheck,
        secrets: ProviderSecretStore,
        drafts: OnboardingDraftStore,
    ) -> None:
        """Create the workflow from injected provider and persistence boundaries."""
        self._checker = checker
        self._secrets = secrets
        self._drafts = drafts
        self._connection_lock = asyncio.Lock()

    def state(self) -> ProviderOnboardingState:
        """Return the current secret-free provider onboarding state.

        Returns:
            Resumable provider state with only secret presence exposed.
        """
        state = self._drafts.load()
        if (
            state.connected
            and state.provider in API_PROVIDERS
            and not self._secrets.has_secret(state.provider)
        ):
            return ProviderOnboardingState(
                provider=state.provider,
                connected=False,
                detail="The saved API key is no longer available. Enter it and retry.",
            )
        return self._with_secret_state(state)

    async def test_connection(
        self,
        provider: ProviderName,
        *,
        api_key: str | None = None,
        region: str | None = None,
        profile: str | None = None,
        endpoint: str | None = None,
    ) -> ProviderOnboardingState:
        """Check a provider and persist successful secret-free evidence.

        Args:
            provider: Provider selected by the user.
            api_key: Optional new API key; omitted to reuse a stored key.

        Returns:
            Redacted connection state for display and model selection.
        """
        async with self._connection_lock:
            resolved = api_key
            if provider in API_PROVIDERS and not resolved:
                resolved = self._secrets.resolve(provider)
            if provider == "amazon_bedrock":
                result = await self._checker.check(
                    provider,
                    region=region,
                    profile=profile,
                )
            elif provider == "azure_openai":
                result = await self._checker.check(
                    provider,
                    api_key=resolved,
                    endpoint=endpoint,
                )
            else:
                result = await self._checker.check(provider, api_key=resolved)
            if not result.connected:
                state = self._drafts.save_connection(result)
                return self._with_secret_state(state)
            if provider in API_PROVIDERS and api_key:
                self._secrets.save_draft(provider, api_key)
            state = self._drafts.save_connection(result)
            return self._with_secret_state(state)

    def save_models(
        self,
        provider: ProviderName,
        quick_model: str,
        detailed_model: str,
        deployment_pricing: tuple[DeploymentPricing, ...] = (),
    ) -> ProviderOnboardingState:
        """Save provider-scoped Quick and Detailed model choices.

        Args:
            provider: Previously verified provider.
            quick_model: Model for Quick evaluation.
            detailed_model: Model for Detailed review.

        Returns:
            Updated resumable provider state.

        Raises:
            ValueError: If the provider key is missing or a model is unavailable.
        """
        if provider in API_PROVIDERS and not self._secrets.has_secret(provider):
            raise ValueError("Enter the API key and test this provider again")
        state = self._drafts.save_models(
            provider,
            quick_model,
            detailed_model,
            deployment_pricing,
        )
        return self._with_secret_state(state)

    def _with_secret_state(
        self, state: ProviderOnboardingState
    ) -> ProviderOnboardingState:
        return ProviderOnboardingState(
            provider=state.provider,
            connected=state.connected,
            detail=state.detail,
            models=state.models,
            has_secret=self._has_secret(state.provider),
            quick_model=state.quick_model,
            detailed_model=state.detailed_model,
            region=state.region,
            profile=state.profile,
            endpoint=state.endpoint,
            deployment_pricing=state.deployment_pricing,
            pricing_catalog=state.pricing_catalog,
        )

    def _has_secret(self, provider: ProviderName | None) -> bool:
        if provider is None or provider not in API_PROVIDERS:
            return False
        return self._secrets.has_secret(provider)


__all__ = ["OnboardingProviderService", "ProviderCheck"]
