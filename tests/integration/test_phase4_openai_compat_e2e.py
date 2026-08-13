"""True end-to-end tests for the ``openai-compat`` LLM backend.

Unlike ``tests/unit/test_openai_compat_llm.py`` (which injects a hand-built fake
client), these tests drive the FULL path through the REAL ``openai`` SDK:

    build_llm_client("openai-compat/...") -> real AsyncOpenAI client
        -> SDK request building (httpx) -> respx intercept -> SDK response
        -> pydantic parsing -> our _extract_* helpers -> LLMResponse

``respx`` intercepts the SDK's underlying ``httpx`` transport, so the genuine
SDK code (request serialisation + pydantic ``ChatCompletion`` parsing) runs.
This exercises everything the fake-client unit tests CANNOT: that the real SDK
accepts our exact ``create(...)`` kwargs, serialises them to the wire shape we
expect, and that our attribute-based ``_extract_*`` helpers survive real
pydantic model objects (not ``SimpleNamespace`` stand-ins).

No PostgreSQL required — runs under ``make quality`` by default.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from jobfeed.adapters.llm._factory import (
    LLMRuntimeUnavailable,
    build_llm_client,
)
from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.config import LLMSettings, Settings
from jobfeed.domain.models import LLMRequest, Message
from jobfeed.observability import get_logger

OPENAI_BASE_URL = "https://api.openai.test/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.test/v1"
API_KEY_ENV = "OPENAI_API_KEY"
PRICED_MODEL = "gpt-4o-mini"
UNPRICED_MODEL = "deepseek-chat"

PROMPT_TOKENS = 12
COMPLETION_TOKENS = 7
MAX_TOKENS = 256
RESPONSE_CONTENT = "Score: 84"
PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_fake_sdk_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep fake SDK endpoints inside respx regardless of host proxy settings."""
    for name in PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(
    *,
    base_url: str = OPENAI_BASE_URL,
    api_key_env: str = API_KEY_ENV,
) -> Settings:
    """Build real validated Settings pointed at a fake openai-compat host."""
    return Settings(
        llm=LLMSettings(
            openai_compat_base_url=base_url,
            openai_compat_api_key_env=api_key_env,
            openai_compat_timeout_s=30.0,
        )
    )


def _chat_completions_body(
    *,
    model: str = PRICED_MODEL,
    content: str = RESPONSE_CONTENT,
    include_usage: bool = True,
    cached_tokens: int | None = None,
) -> dict[str, Any]:
    """A realistic OpenAI ``chat.completions`` JSON response body."""
    body: dict[str, Any] = {
        "id": "chatcmpl-e2e-001",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if include_usage:
        usage: dict[str, Any] = {
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": COMPLETION_TOKENS,
            "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS,
        }
        if cached_tokens is not None:
            usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
        body["usage"] = usage
    return body


def _request(model: str = PRICED_MODEL) -> LLMRequest:
    return LLMRequest(
        messages=[
            Message(role="system", content="you are a job scorer"),
            Message(role="user", content="score this posting"),
        ],
        model=model,
        temperature=0.0,
        max_tokens=MAX_TOKENS,
        # A schema is set on the domain request; the LCD wire contract must
        # still NOT translate it into any vendor field.
        response_schema={"type": "object"},
    )


def _build(settings: Settings, model: str = PRICED_MODEL) -> Any:
    return build_llm_client(
        f"openai-compat/{model}",
        settings=settings.llm,
        price_table=load_price_table(),
        logger=get_logger(),
    )


# ---------------------------------------------------------------------------
# Tests: real SDK round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priced_model_real_sdk_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full real-SDK round-trip on a priced model: parsed response + cost > 0.

    Also asserts the EXACT wire request the real SDK emitted: only
    model/messages/temperature/max_tokens, no response_format/json_schema or
    vendor fields.
    """
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    client = _build(_settings())

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_chat_completions_body())
        )

        response = await client.complete(_request())

    # --- Parsed LLMResponse ---
    assert response.content == RESPONSE_CONTENT
    assert response.model == PRICED_MODEL
    assert response.input_tokens == PROMPT_TOKENS
    assert response.output_tokens == COMPLETION_TOKENS
    assert isinstance(response.cost_usd, float)
    assert response.cost_usd > 0.0
    assert response.cached is False
    assert response.latency_ms >= 0

    # --- The request the real SDK actually serialised onto the wire ---
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == PRICED_MODEL
    assert sent["messages"] == [
        {"role": "system", "content": "you are a job scorer"},
        {"role": "user", "content": "score this posting"},
    ]
    assert sent["temperature"] == 0.0
    assert sent["max_tokens"] == MAX_TOKENS
    # LCD contract: no schema / response_format / vendor fields on the wire.
    assert "response_format" not in sent
    assert "json_schema" not in sent
    assert "response_schema" not in sent
    assert set(sent) == {"model", "messages", "temperature", "max_tokens"}
    # Authorization header carries the configured api key.
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_cached_tokens_flag_through_real_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cached_tokens > 0 in real pydantic usage details sets ``cached``."""
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    client = _build(_settings())

    with respx.mock:
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_chat_completions_body(cached_tokens=8)
            )
        )
        response = await client.complete(_request())

    assert response.cached is True
    assert response.input_tokens == PROMPT_TOKENS


@pytest.mark.asyncio
async def test_unpriced_model_second_provider_by_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DeepSeek-style base_url + unpriced model: same adapter, cost None.

    Proves "≥2 providers by base_url alone" through the REAL SDK — only the
    configured base_url and model differ; no per-provider code path.
    """
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    client = _build(_settings(base_url=DEEPSEEK_BASE_URL), model=UNPRICED_MODEL)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{DEEPSEEK_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_chat_completions_body(model=UNPRICED_MODEL, content="deepseek"),
            )
        )
        response = await client.complete(_request(model=UNPRICED_MODEL))

    assert route.called
    assert response.content == "deepseek"
    assert response.model == UNPRICED_MODEL
    # Unpriced model => best-effort cost is None (NOT estimate_cost's 0.0).
    assert response.cost_usd is None


@pytest.mark.asyncio
async def test_omitted_usage_through_real_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama-style response with no ``usage`` parses to zero tokens, no crash.

    The real SDK builds a ``ChatCompletion`` whose ``.usage`` is ``None``; our
    ``_extract_usage`` must guard that against the genuine pydantic object.
    """
    monkeypatch.setenv(API_KEY_ENV, "sk-test")
    client = _build(_settings())

    with respx.mock:
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_chat_completions_body(include_usage=False)
            )
        )
        response = await client.complete(_request())

    assert response.content == RESPONSE_CONTENT
    assert response.input_tokens == 0
    assert response.output_tokens == 0
    assert response.cached is False
    # Priced model but zero tokens => estimate is a real 0.0 (not None).
    assert response.cost_usd == 0.0


@pytest.mark.asyncio
async def test_missing_api_key_raises_before_any_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No api-key env => build_llm_client raises before any HTTP is attempted."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with respx.mock as mock:
        route = mock.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_chat_completions_body())
        )

        with pytest.raises(LLMRuntimeUnavailable):
            _build(_settings())

    # The route must never have been hit — failure is pre-network.
    assert not route.called
