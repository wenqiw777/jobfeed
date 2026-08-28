"""Real OpenAI SDK round-trip through the Azure v1 factory route."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from jobfeed.adapters.llm._factory import LLMClientBuildOptions, build_llm_client
from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.config import LLMSettings
from jobfeed.domain.models import LLMRequest, Message
from jobfeed.observability import get_logger

ENDPOINT = "https://jobfeed.openai.azure.com/openai/v1"
DEPLOYMENT = "jobfeed-quick"
GPT5_MAX_TOKENS = 64


@pytest.mark.asyncio
async def test_azure_v1_uses_deployment_alias_and_confirmed_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    client = build_llm_client(
        f"azure-openai/{DEPLOYMENT}",
        settings=LLMSettings(
            azure_openai_endpoint=ENDPOINT,
            azure_deployment_pricing=[
                {
                    "deployment": DEPLOYMENT,
                    "base_model": "gpt-4.1-mini",
                    "input_usd_per_million": 1.0,
                    "output_usd_per_million": 4.0,
                    "cached_input_usd_per_million": 0.25,
                }
            ],
        ),
        price_table=load_price_table(),
        logger=get_logger(),
        options=LLMClientBuildOptions(
            max_retries=0,
            api_key_overrides={"azure-openai": "local-azure-secret"},
        ),
    )
    response_body = {
        "id": "chatcmpl-azure-1",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Score: 88"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1_000,
            "completion_tokens": 100,
            "total_tokens": 1_100,
            "prompt_tokens_details": {"cached_tokens": 200},
        },
    }

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{ENDPOINT}/chat/completions").mock(
            return_value=httpx.Response(200, json=response_body)
        )
        response = await client.complete(
            LLMRequest(
                messages=[Message(role="user", content="Score this job")],
                model=DEPLOYMENT,
            )
        )

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == DEPLOYMENT
    assert route.calls.last.request.headers["authorization"] == (
        "Bearer local-azure-secret"
    )
    assert response.model == DEPLOYMENT
    assert response.cached is True
    assert response.cost_usd == pytest.approx(0.00125)


@pytest.mark.asyncio
async def test_azure_gpt5_uses_supported_chat_completion_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPT-5 deployments require the new token limit and default temperature."""
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    deployment = "jobfeed-gpt-5-mini"
    client = build_llm_client(
        f"azure-openai/{deployment}",
        settings=LLMSettings(
            azure_openai_endpoint=ENDPOINT,
            azure_deployment_pricing=[
                {
                    "deployment": deployment,
                    "base_model": "gpt-5-mini",
                    "input_usd_per_million": 0.25,
                    "output_usd_per_million": 2.0,
                    "cached_input_usd_per_million": 0.025,
                }
            ],
        ),
        price_table=load_price_table(),
        logger=get_logger(),
        options=LLMClientBuildOptions(
            max_retries=0,
            api_key_overrides={"azure-openai": "local-azure-secret"},
        ),
    )
    response_body = {
        "id": "chatcmpl-azure-gpt5",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "gpt-5-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
    }

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{ENDPOINT}/chat/completions").mock(
            return_value=httpx.Response(200, json=response_body)
        )
        await client.complete(
            LLMRequest(
                messages=[Message(role="user", content="Reply OK")],
                model=deployment,
                temperature=0.0,
                max_tokens=GPT5_MAX_TOKENS,
            )
        )

    sent = json.loads(route.calls.last.request.content)
    assert sent["max_completion_tokens"] == GPT5_MAX_TOKENS
    assert "max_tokens" not in sent
    assert "temperature" not in sent
