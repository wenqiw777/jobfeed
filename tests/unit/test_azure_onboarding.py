"""Azure OpenAI onboarding contracts: connection, secrets, and confirmed prices."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_providers import ProviderChecker
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_state import OnboardingDraftStore
from jobfeed.onboarding_types import DeploymentPricing

PRIVATE_FILE_MODE = 0o600


async def test_azure_v1_models_are_not_labeled_as_deployments() -> None:
    """The v1 model catalog verifies access but is not deployment discovery."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://jobfeed-eastus.openai.azure.com/openai/v1/models"
        )
        assert request.headers["Authorization"] == "Bearer azure-secret"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "jobfeed-detailed", "object": "model"},
                    {"id": "jobfeed-quick", "object": "model"},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ProviderChecker(http_client=client).check(
            "azure_openai",
            api_key="azure-secret",
            endpoint="https://jobfeed-eastus.openai.azure.com/",
        )

    assert result.connected is True
    assert result.endpoint == "https://jobfeed-eastus.openai.azure.com/openai/v1"
    assert result.models == ()
    assert "deployment aliases" in result.detail
    reference = next(
        price for price in result.pricing_catalog if price.base_model == "gpt-4.1-mini"
    )
    assert reference.input_usd_per_million > 0
    assert reference.output_usd_per_million > 0


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://jobfeed.openai.azure.com",
        "https://user:password@jobfeed.openai.azure.com",
        "https://jobfeed.openai.azure.com/openai/v1?api-version=legacy",
    ],
)
async def test_azure_rejects_unsafe_or_legacy_endpoints(endpoint: str) -> None:
    result = await ProviderChecker().check(
        "azure_openai",
        api_key="azure-secret",
        endpoint=endpoint,
    )

    assert result.connected is False
    assert result.models == ()
    assert "endpoint" in result.detail.lower()


async def test_azure_state_round_trips_prices_without_leaking_key(
    tmp_path: Path,
) -> None:
    endpoint = "https://jobfeed.openai.azure.com/openai/v1"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-4.1-mini"}]})

    secret_path = tmp_path / "data" / "secrets.toml"
    draft_path = tmp_path / "data" / "onboarding.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = OnboardingProviderService(
            checker=ProviderChecker(http_client=client),
            secrets=ProviderSecretStore(secret_path),
            drafts=OnboardingDraftStore(draft_path),
        )
        connected = await service.test_connection(
            "azure_openai",
            api_key="azure-secret",
            endpoint=endpoint,
        )
        selected = service.save_models(
            "azure_openai",
            "quick-prod",
            "quick-prod",
            deployment_pricing=(
                DeploymentPricing(
                    deployment="quick-prod",
                    base_model="gpt-4.1-mini",
                    input_usd_per_million=0.4,
                    output_usd_per_million=1.6,
                    cached_input_usd_per_million=0.1,
                ),
            ),
        )
        rechecked = await service.test_connection(
            "azure_openai",
            endpoint=endpoint,
        )

    resumed = service.state()
    assert connected.endpoint == endpoint
    assert rechecked.quick_model == selected.quick_model
    assert rechecked.deployment_pricing == selected.deployment_pricing
    assert resumed == rechecked
    assert resumed.deployment_pricing[0].base_model == "gpt-4.1-mini"
    assert resumed.has_secret is True
    assert stat.S_IMODE(secret_path.stat().st_mode) == PRIVATE_FILE_MODE
    assert "azure-secret" not in draft_path.read_text(encoding="utf-8")
    assert "azure-secret" in secret_path.read_text(encoding="utf-8")
    assert json.loads(draft_path.read_text(encoding="utf-8"))["endpoint"] == endpoint


async def test_azure_requires_confirmed_prices_for_each_selected_deployment(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4.1-mini"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = OnboardingProviderService(
            checker=ProviderChecker(http_client=client),
            secrets=ProviderSecretStore(tmp_path / "data" / "secrets.toml"),
            drafts=OnboardingDraftStore(tmp_path / "data" / "onboarding.json"),
        )
        await service.test_connection(
            "azure_openai",
            api_key="azure-secret",
            endpoint="https://jobfeed.openai.azure.com/openai/v1",
        )

    with pytest.raises(ValueError, match="confirmed pricing"):
        service.save_models(
            "azure_openai",
            "quick",
            "detailed",
            deployment_pricing=(
                DeploymentPricing(
                    deployment="quick",
                    base_model="gpt-4.1-mini",
                    input_usd_per_million=0.4,
                    output_usd_per_million=1.6,
                ),
            ),
        )
