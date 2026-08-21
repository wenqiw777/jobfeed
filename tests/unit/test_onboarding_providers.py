"""Provider connection checks use injectable HTTP and process boundaries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import httpx
import pytest

import jobfeed.onboarding_providers as provider_module
from jobfeed.onboarding_providers import (
    ProcessResult,
    ProviderChecker,
)


async def test_openai_connection_lists_only_evaluation_models() -> None:
    """OpenAI authentication and model discovery share the official models call."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/models"
        assert request.headers["Authorization"] == "Bearer sk-fake"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.6-sol"},
                    {"id": "text-embedding-3-small"},
                    {"id": "gpt-image-1"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ProviderChecker(http_client=client).check(
            "openai_api", api_key="sk-fake"
        )

    assert result.connected is True
    assert [model.id for model in result.models] == ["gpt-5.6-sol"]


async def test_anthropic_connection_uses_required_headers_and_display_names() -> None:
    """Anthropic's official models endpoint supplies ids and human labels."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.anthropic.com/v1/models?limit=1000"
        assert request.headers["x-api-key"] == "anthropic-fake"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "claude-sonnet-5",
                        "display_name": "Claude Sonnet 5",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ProviderChecker(http_client=client).check(
            "anthropic_api", api_key="anthropic-fake"
        )

    assert result.connected is True
    assert [(model.id, model.label) for model in result.models] == [
        ("claude-sonnet-5", "Claude Sonnet 5")
    ]


async def test_api_authentication_failure_is_specific_and_never_echoes_key() -> None:
    """A rejected key produces a retryable redacted connection result."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad sk-fake"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ProviderChecker(http_client=client).check(
            "openai_api", api_key="sk-fake"
        )

    assert result.connected is False
    assert "rejected" in result.detail.lower()
    assert "sk-fake" not in result.detail


async def test_codex_uses_login_status_then_local_model_catalog() -> None:
    """Codex login and model discovery do not consume a model request."""
    calls: list[Sequence[str]] = []

    async def run(command: Sequence[str]) -> ProcessResult:
        calls.append(command)
        if command == ["codex", "login", "status"]:
            return ProcessResult(
                returncode=0,
                stdout="Logged in using ChatGPT",
                stderr="",
            )
        payload = {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "display_name": "GPT-5.6 Sol",
                    "visibility": "list",
                },
                {
                    "slug": "hidden-model",
                    "display_name": "Hidden",
                    "visibility": "hide",
                },
            ]
        }
        return ProcessResult(returncode=0, stdout=json.dumps(payload), stderr="")

    checker = ProviderChecker(
        process_runner=run,
        executable_lookup=lambda name: f"/fake/{name}",
    )
    result = await checker.check("codex_cli")

    assert calls == [
        ["codex", "login", "status"],
        ["codex", "debug", "models"],
    ]
    assert result.connected is True
    assert [(model.id, model.label) for model in result.models] == [
        ("gpt-5.6-sol", "GPT-5.6 Sol")
    ]


async def test_claude_uses_auth_status_and_derived_cli_models() -> None:
    """Claude auth status verifies the local login without an inference call."""

    async def run(command: Sequence[str]) -> ProcessResult:
        assert command == ["claude", "auth", "status"]
        return ProcessResult(
            returncode=0,
            stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
            stderr="",
        )

    checker = ProviderChecker(
        process_runner=run,
        executable_lookup=lambda name: f"/fake/{name}",
    )
    result = await checker.check("claude_cli")

    assert result.connected is True
    assert {model.id for model in result.models} == {"haiku", "sonnet", "opus"}


async def test_cancelled_cli_check_kills_and_reaps_child_process(monkeypatch) -> None:
    """A disconnected request must not leave its CLI probe running."""

    class HangingProcess:
        returncode: int | None = None

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.Future()

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.waited = True
            return -9

    process = HangingProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(
        provider_module.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(provider_module._run_process(["codex"]), timeout=0.01)

    assert process.killed is True
    assert process.waited is True
