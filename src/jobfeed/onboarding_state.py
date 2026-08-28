"""Secret-free, resumable draft persistence for provider onboarding."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from jobfeed.onboarding_types import (
    ConnectionResult,
    DeploymentPricing,
    ModelPriceReference,
    ProviderModel,
    ProviderName,
    ProviderOnboardingState,
)


class OnboardingDraftStore:
    """Atomically persist provider connection and model-selection draft state."""

    def __init__(self, path: Path) -> None:
        """Create a draft store rooted at ``data/onboarding.json``."""
        self._path = path.resolve()

    def load(self) -> ProviderOnboardingState:
        """Load the current provider draft, returning an empty state when absent.

        Returns:
            Parsed secret-free draft or a new empty state.
        """
        if not self._path.exists():
            return ProviderOnboardingState()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        models = tuple(ProviderModel(**model) for model in raw.get("models", []))
        deployment_pricing = tuple(
            DeploymentPricing(**price) for price in raw.get("deployment_pricing", [])
        )
        pricing_catalog = tuple(
            ModelPriceReference(**price) for price in raw.get("pricing_catalog", [])
        )
        return ProviderOnboardingState(
            provider=raw.get("provider"),
            connected=bool(raw.get("connected", False)),
            detail=raw.get("detail"),
            models=models,
            quick_model=raw.get("quick_model"),
            detailed_model=raw.get("detailed_model"),
            region=raw.get("region"),
            profile=raw.get("profile"),
            endpoint=raw.get("endpoint"),
            deployment_pricing=deployment_pricing,
            pricing_catalog=pricing_catalog,
        )

    def save_connection(self, result: ConnectionResult) -> ProviderOnboardingState:
        """Replace connection evidence and invalidate incompatible choices.

        Args:
            result: Latest redacted provider check result.

        Returns:
            Persisted draft with compatible selections retained.
        """
        previous = self.load()
        available = {model.id for model in result.models}
        state = ProviderOnboardingState(
            provider=result.provider,
            connected=result.connected,
            detail=result.detail,
            models=result.models,
            quick_model=_preserve_choice(previous, result.provider, available, True),
            detailed_model=_preserve_choice(
                previous, result.provider, available, False
            ),
            region=result.region,
            profile=result.profile,
            endpoint=result.endpoint,
            deployment_pricing=_preserve_pricing(
                previous,
                result.provider,
                result.endpoint,
                available,
            ),
            pricing_catalog=result.pricing_catalog,
        )
        self._write(state)
        return state

    def save_models(
        self,
        provider: ProviderName,
        quick_model: str,
        detailed_model: str,
        deployment_pricing: tuple[DeploymentPricing, ...] = (),
    ) -> ProviderOnboardingState:
        """Persist two model choices after validating the verified catalog.

        Args:
            provider: Previously verified provider.
            quick_model: Quick evaluation model id.
            detailed_model: Detailed review model id.

        Returns:
            Persisted provider draft.

        Raises:
            ValueError: If the provider is unverified or a model is unavailable.
        """
        current = self.load()
        if not current.connected or current.provider != provider:
            raise ValueError("Test this provider connection before choosing models")
        available = {model.id for model in current.models}
        if provider == "azure_openai":
            if not quick_model.strip() or not detailed_model.strip():
                raise ValueError("Enter both Azure deployment aliases")
        elif quick_model not in available or detailed_model not in available:
            raise ValueError("Choose models returned by the verified provider")
        if provider == "azure_openai":
            _validate_azure_pricing(deployment_pricing, {quick_model, detailed_model})
        updated = ProviderOnboardingState(
            provider=provider,
            connected=True,
            detail=current.detail,
            models=current.models,
            quick_model=quick_model,
            detailed_model=detailed_model,
            region=current.region,
            profile=current.profile,
            endpoint=current.endpoint,
            deployment_pricing=deployment_pricing,
            pricing_catalog=current.pricing_catalog,
        )
        self._write(updated)
        return updated

    def _write(self, state: ProviderOnboardingState) -> None:
        document = {
            "provider": state.provider,
            "connected": state.connected,
            "detail": state.detail,
            "models": [model.__dict__ for model in state.models],
            "quick_model": state.quick_model,
            "detailed_model": state.detailed_model,
            "region": state.region,
            "profile": state.profile,
            "endpoint": state.endpoint,
            "deployment_pricing": [
                price.__dict__ for price in state.deployment_pricing
            ],
            "pricing_catalog": [price.__dict__ for price in state.pricing_catalog],
        }
        content = json.dumps(document, indent=2, sort_keys=True) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _preserve_choice(
    previous: ProviderOnboardingState,
    provider: ProviderName,
    available: set[str],
    is_quick: bool,
) -> str | None:
    if previous.provider != provider:
        return None
    choice = previous.quick_model if is_quick else previous.detailed_model
    if provider == "azure_openai":
        return choice
    return choice if choice in available else None


def _preserve_pricing(
    previous: ProviderOnboardingState,
    provider: ProviderName,
    endpoint: str | None,
    available: set[str],
) -> tuple[DeploymentPricing, ...]:
    if previous.provider != provider or previous.endpoint != endpoint:
        return ()
    if provider == "azure_openai":
        return previous.deployment_pricing
    return tuple(
        price for price in previous.deployment_pricing if price.deployment in available
    )


def _validate_azure_pricing(
    prices: tuple[DeploymentPricing, ...],
    selected: set[str],
) -> None:
    by_deployment = {price.deployment: price for price in prices}
    if len(by_deployment) != len(prices) or set(by_deployment) != selected:
        raise ValueError("Provide confirmed pricing for each selected deployment")
    for price in prices:
        if not price.deployment.strip() or not price.base_model.strip():
            raise ValueError("Provide confirmed pricing for each selected deployment")
        values = (
            price.input_usd_per_million,
            price.output_usd_per_million,
            price.cached_input_usd_per_million,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("Confirmed deployment prices must not be negative")


__all__ = ["OnboardingDraftStore"]
