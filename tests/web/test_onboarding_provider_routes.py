"""Web contracts for provider onboarding and secret redaction."""

from __future__ import annotations

import json
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_providers import ConnectionResult, ProviderModel
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_state import OnboardingDraftStore
from jobfeed.onboarding_types import ProviderName
from jobfeed.web.app import create_web_app

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
PRIVATE_FILE_MODE = 0o600


class FakeChecker:
    """Deterministic provider checker for route contract tests."""

    async def check(
        self, provider: ProviderName, *, api_key: str | None = None
    ) -> ConnectionResult:
        """Return one provider-scoped model while recording no secret."""
        assert provider == "openai_api"
        assert api_key == "sk-route-secret"
        return ConnectionResult(
            provider="openai_api",
            connected=True,
            detail="OpenAI API connection verified.",
            models=(ProviderModel(id="gpt-5.6-sol", label="GPT-5.6 Sol"),),
        )


class SucceedsThenFailsChecker:
    """Return a verified catalog once, then a redacted retry failure."""

    def __init__(self) -> None:
        self.calls = 0

    async def check(
        self, provider: ProviderName, *, api_key: str | None = None
    ) -> ConnectionResult:
        """Model a credential that is revoked between two checks."""
        assert api_key is not None
        self.calls += 1
        if self.calls == 1:
            return ConnectionResult(
                provider=provider,
                connected=True,
                detail="OpenAI API connection verified.",
                models=(ProviderModel(id="gpt-5.6-sol", label="GPT-5.6 Sol"),),
            )
        return ConnectionResult(
            provider=provider,
            connected=False,
            detail="The API key was rejected. Check it and retry.",
        )


class FakeBedrockChecker:
    async def check(
        self,
        provider: ProviderName,
        *,
        api_key: str | None = None,
        region: str | None = None,
        profile: str | None = None,
    ) -> ConnectionResult:
        assert provider == "amazon_bedrock"
        assert api_key is None
        assert region == "us-east-1"
        assert profile == "default"
        return ConnectionResult(
            provider=provider,
            connected=True,
            detail="Amazon Bedrock credentials and model catalog verified.",
            models=(
                ProviderModel(
                    id="us.anthropic.claude-sonnet-5",
                    label="Claude Sonnet 5 · Inference profile",
                    kind="inference_profile",
                    pricing_model="anthropic.claude-sonnet-5",
                ),
            ),
            region=region,
            profile=profile,
        )


class FakeAzureChecker:
    async def check(
        self,
        provider: ProviderName,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
    ) -> ConnectionResult:
        assert provider == "azure_openai"
        assert api_key == "azure-route-secret"
        assert endpoint == "https://jobfeed.openai.azure.com/openai/v1"
        return ConnectionResult(
            provider=provider,
            connected=True,
            detail="Azure OpenAI connection verified. Enter deployment aliases.",
            models=(),
            endpoint=endpoint,
        )


@asynccontextmanager
async def _open_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


async def test_connection_and_model_selection_are_resumable_and_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    """A successful API check stores only the secret file's private value."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    app = create_web_app()
    app.state.onboarding_provider_service = OnboardingProviderService(
        checker=FakeChecker(),
        secrets=ProviderSecretStore(tmp_path / "data" / "secrets.toml"),
        drafts=OnboardingDraftStore(tmp_path / "data" / "onboarding.json"),
    )

    async with _open_client(app) as client:
        checked = await client.post(
            "/api/onboarding/provider/test",
            json={"provider": "openai_api", "api_key": "sk-route-secret"},
        )
        selected = await client.put(
            "/api/onboarding/provider/models",
            json={
                "provider": "openai_api",
                "quick_model": "gpt-5.6-sol",
                "detailed_model": "gpt-5.6-sol",
            },
        )
        resumed = await client.get("/api/onboarding/provider")
        configuration = await client.get("/api/config")

    assert checked.status_code == HTTP_OK, checked.text
    assert selected.status_code == HTTP_OK, selected.text
    assert resumed.status_code == HTTP_OK, resumed.text
    serialized = checked.text + selected.text + resumed.text + configuration.text
    assert "sk-route-secret" not in serialized
    assert resumed.json()["has_secret"] is True
    assert resumed.json()["quick_model"] == "gpt-5.6-sol"
    assert resumed.json()["detailed_model"] == "gpt-5.6-sol"
    assert configuration.json()["configured"] is False
    assert not (tmp_path / "config.toml").exists()

    secrets_path = tmp_path / "data" / "secrets.toml"
    assert "sk-route-secret" in secrets_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(secrets_path.stat().st_mode) == PRIVATE_FILE_MODE
    assert "sk-route-secret" not in (tmp_path / "data" / "onboarding.json").read_text(
        encoding="utf-8"
    )
    assert (
        json.loads((tmp_path / "data" / "onboarding.json").read_text(encoding="utf-8"))[
            "provider"
        ]
        == "openai_api"
    )


async def test_failed_recheck_replaces_stale_connected_draft(tmp_path: Path) -> None:
    """A failed retry remains failed after refresh instead of reviving models."""
    (tmp_path / "data").mkdir()
    app = create_web_app()
    app.state.onboarding_provider_service = OnboardingProviderService(
        checker=SucceedsThenFailsChecker(),
        secrets=ProviderSecretStore(tmp_path / "data" / "secrets.toml"),
        drafts=OnboardingDraftStore(tmp_path / "data" / "onboarding.json"),
    )

    async with _open_client(app) as client:
        first = await client.post(
            "/api/onboarding/provider/test",
            json={"provider": "openai_api", "api_key": "first-key"},
        )
        failed = await client.post(
            "/api/onboarding/provider/test",
            json={"provider": "openai_api", "api_key": "revoked-key"},
        )
        resumed = await client.get("/api/onboarding/provider")

    assert first.json()["connected"] is True
    assert failed.json()["connected"] is False
    assert resumed.json()["connected"] is False
    assert resumed.json()["models"] == []
    assert resumed.json()["detail"] == "The API key was rejected. Check it and retry."


async def test_bedrock_route_round_trips_region_profile_and_model_kind(
    tmp_path: Path,
) -> None:
    (tmp_path / "data").mkdir()
    app = create_web_app()
    app.state.onboarding_provider_service = OnboardingProviderService(
        checker=FakeBedrockChecker(),
        secrets=ProviderSecretStore(tmp_path / "data" / "secrets.toml"),
        drafts=OnboardingDraftStore(tmp_path / "data" / "onboarding.json"),
    )

    async with _open_client(app) as client:
        checked = await client.post(
            "/api/onboarding/provider/test",
            json={
                "provider": "amazon_bedrock",
                "region": "us-east-1",
                "profile": "default",
            },
        )

    assert checked.status_code == HTTP_OK, checked.text
    assert checked.json()["region"] == "us-east-1"
    assert checked.json()["profile"] == "default"
    assert checked.json()["models"] == [
        {
            "id": "us.anthropic.claude-sonnet-5",
            "label": "Claude Sonnet 5 · Inference profile",
            "kind": "inference_profile",
        }
    ]
    assert checked.json()["has_secret"] is False


async def test_azure_route_round_trips_endpoint_and_confirmed_deployment_prices(
    tmp_path: Path,
) -> None:
    (tmp_path / "data").mkdir()
    app = create_web_app()
    app.state.onboarding_provider_service = OnboardingProviderService(
        checker=FakeAzureChecker(),
        secrets=ProviderSecretStore(tmp_path / "data" / "secrets.toml"),
        drafts=OnboardingDraftStore(tmp_path / "data" / "onboarding.json"),
    )

    async with _open_client(app) as client:
        checked = await client.post(
            "/api/onboarding/provider/test",
            json={
                "provider": "azure_openai",
                "api_key": "azure-route-secret",
                "endpoint": "https://jobfeed.openai.azure.com/openai/v1",
            },
        )
        selected = await client.put(
            "/api/onboarding/provider/models",
            json={
                "provider": "azure_openai",
                "quick_model": "quick-prod",
                "detailed_model": "quick-prod",
                "deployment_pricing": [
                    {
                        "deployment": "quick-prod",
                        "base_model": "gpt-4.1-mini",
                        "input_usd_per_million": 0.4,
                        "output_usd_per_million": 1.6,
                        "cached_input_usd_per_million": 0.1,
                    }
                ],
            },
        )

    assert checked.status_code == HTTP_OK, checked.text
    assert checked.json()["endpoint"] == ("https://jobfeed.openai.azure.com/openai/v1")
    assert checked.json()["models"] == []
    assert selected.status_code == HTTP_OK, selected.text
    assert selected.json()["deployment_pricing"] == [
        {
            "deployment": "quick-prod",
            "base_model": "gpt-4.1-mini",
            "input_usd_per_million": 0.4,
            "output_usd_per_million": 1.6,
            "cached_input_usd_per_million": 0.1,
        }
    ]
    assert "azure-route-secret" not in checked.text + selected.text


async def test_onboarding_mutations_document_shared_422_error_shape(
    tmp_path: Path,
) -> None:
    """Generated clients see the same error envelope the middleware returns."""
    (tmp_path / "data").mkdir()
    app = create_web_app()
    schema = app.openapi()

    for path, method in (
        ("/api/onboarding/provider/test", "post"),
        ("/api/onboarding/provider/models", "put"),
    ):
        error_schema = schema["paths"][path][method]["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert error_schema == {"$ref": "#/components/schemas/ApiErrorResponse"}


async def test_missing_api_secret_invalidates_cached_connection(tmp_path: Path) -> None:
    """Deleting a verified key makes resume and model save fail closed."""
    (tmp_path / "data").mkdir()
    secret_store = ProviderSecretStore(tmp_path / "data" / "secrets.toml")
    app = create_web_app()
    app.state.onboarding_provider_service = OnboardingProviderService(
        checker=FakeChecker(),
        secrets=secret_store,
        drafts=OnboardingDraftStore(tmp_path / "data" / "onboarding.json"),
    )

    async with _open_client(app) as client:
        checked = await client.post(
            "/api/onboarding/provider/test",
            json={"provider": "openai_api", "api_key": "sk-route-secret"},
        )
        secret_store.delete_draft("openai_api")
        resumed = await client.get("/api/onboarding/provider")
        selected = await client.put(
            "/api/onboarding/provider/models",
            json={
                "provider": "openai_api",
                "quick_model": "gpt-5.6-sol",
                "detailed_model": "gpt-5.6-sol",
            },
        )

    assert checked.json()["connected"] is True
    assert resumed.json()["connected"] is False
    assert resumed.json()["models"] == []
    assert selected.status_code == HTTP_UNPROCESSABLE
    assert selected.json()["error"]["code"] == "invalid_model_selection"
