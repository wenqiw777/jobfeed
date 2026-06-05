"""Unit tests for OpenAiCompatLLM — fake injected client, no real SDK/HTTP.

The adapter takes an injected ``AsyncOpenAI``-shaped client, so every test here
passes a hand-built fake exposing an async ``chat.completions.create`` returning
a stub response object.  No monkeypatching the SDK; no network.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from jobfeed.adapters.llm._pricing import ModelPricing
from jobfeed.adapters.llm.openai_compat import OpenAiCompatLLM
from jobfeed.domain.models import LLMRequest, Message
from jobfeed.observability import get_logger
from jobfeed.ports.llm import LLMClient

PRICED_MODEL = "gpt-4o-mini"
DEFAULT_MAX_TOKENS = 4096
INPUT_TOKENS = 12
OUTPUT_TOKENS = 7
PRICE_TABLE: dict[str, ModelPricing] = {
    PRICED_MODEL: ModelPricing(
        input_cost_per_token=1e-6,
        output_cost_per_token=2e-6,
    )
}


# ---------------------------------------------------------------------------
# Fake injected client
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []
        self.base_url: str | None = None

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    """Mimics the AsyncOpenAI client surface the adapter touches."""

    def __init__(self, response: object, *, base_url: str = "https://x/v1") -> None:
        self.completions = _FakeCompletions(response)
        self.chat = _FakeChat(self.completions)
        self.base_url = base_url


def _make_response(
    content: str = "ok",
    *,
    prompt_tokens: int | None = 10,
    completion_tokens: int | None = 5,
    cached_tokens: int | None = None,
    omit_usage: bool = False,
) -> object:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    if omit_usage:
        usage: object | None = None
    else:
        details = (
            SimpleNamespace(cached_tokens=cached_tokens)
            if cached_tokens is not None
            else None
        )
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=details,
        )
    return SimpleNamespace(choices=[choice], usage=usage)


def _request(model: str = PRICED_MODEL) -> LLMRequest:
    return LLMRequest(
        messages=[
            Message(role="system", content="you are a scorer"),
            Message(role="user", content="score this job"),
        ],
        model=model,
        response_schema={"type": "object"},
    )


def _build(client: _FakeClient, *, model: str = PRICED_MODEL) -> OpenAiCompatLLM:
    return OpenAiCompatLLM(
        client=client,  # type: ignore[arg-type]
        model=model,
        price_table=PRICE_TABLE,
        logger=get_logger(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_is_llm_client() -> None:
    """OpenAiCompatLLM satisfies the runtime-checkable LLMClient protocol."""
    adapter = _build(_FakeClient(_make_response()))
    assert isinstance(adapter, LLMClient)


@pytest.mark.asyncio
async def test_messages_mapped_directly_no_vendor_fields() -> None:
    """Messages map straight through; no response_format / schema / vendor keys."""
    client = _FakeClient(_make_response())
    adapter = _build(client)

    await adapter.complete(_request())

    captured = client.completions.calls[0]
    assert captured["messages"] == [
        {"role": "system", "content": "you are a scorer"},
        {"role": "user", "content": "score this job"},
    ]
    assert captured["model"] == PRICED_MODEL
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == DEFAULT_MAX_TOKENS
    # response_schema must NOT be translated into any wire field (LCD contract).
    assert "response_format" not in captured
    assert "json_schema" not in captured
    assert "response_schema" not in captured
    assert set(captured) == {"model", "messages", "temperature", "max_tokens"}


@pytest.mark.asyncio
async def test_wire_uses_constructed_model_not_request_spec() -> None:
    """The wire call uses the factory-parsed model, not the routing spec.

    ``EvaluateService`` sets ``LLMRequest.model`` to the full ``backend/model``
    routing spec (e.g. ``openai-compat/gpt-4o-mini``), while the factory parses
    that and injects the bare provider model (``gpt-4o-mini``) at construction.
    The adapter must send the bare model to the provider — sending the spec
    would make providers reject the request and miss the price table.
    """
    client = _FakeClient(_make_response())
    adapter = _build(client, model=PRICED_MODEL)

    response = await adapter.complete(_request(model=f"openai-compat/{PRICED_MODEL}"))

    assert client.completions.calls[0]["model"] == PRICED_MODEL
    assert response.model == PRICED_MODEL


@pytest.mark.asyncio
async def test_cached_tokens_billed_at_discounted_rate() -> None:
    """Cached prompt tokens are billed at the cache-read rate, not full input.

    Providers report cached prompt tokens in
    ``usage.prompt_tokens_details.cached_tokens``. Cost is best-effort, but must
    not bill cached tokens at the full input rate — overstating spend trips the
    daily dollar budget gate early on repeated evaluations with cached prefixes.
    """
    cached_table: dict[str, ModelPricing] = {
        PRICED_MODEL: ModelPricing(
            input_cost_per_token=1e-6,
            output_cost_per_token=2e-6,
            cached_input_cost_per_token=1e-7,
        )
    }
    client = _FakeClient(
        _make_response(prompt_tokens=10, completion_tokens=5, cached_tokens=8)
    )
    adapter = OpenAiCompatLLM(
        client=client,  # type: ignore[arg-type]
        model=PRICED_MODEL,
        price_table=cached_table,
        logger=get_logger(),
    )

    response = await adapter.complete(_request())

    # (10-8) input @ 1e-6 + 8 cached @ 1e-7 + 5 output @ 2e-6 = 1.28e-5,
    # vs 2.0e-5 if the 8 cached tokens were billed at the full input rate.
    assert response.cost_usd == pytest.approx(2e-6 + 8e-7 + 1e-5)
    assert response.cached is True


@pytest.mark.asyncio
async def test_usage_parsed_into_response() -> None:
    """prompt/completion tokens flow into the LLMResponse."""
    adapter = _build(
        _FakeClient(
            _make_response(prompt_tokens=INPUT_TOKENS, completion_tokens=OUTPUT_TOKENS)
        )
    )

    response = await adapter.complete(_request())

    assert response.content == "ok"
    assert response.input_tokens == INPUT_TOKENS
    assert response.output_tokens == OUTPUT_TOKENS
    assert response.model == PRICED_MODEL
    assert response.latency_ms >= 0


@pytest.mark.asyncio
async def test_omitted_usage_defaults_to_zero() -> None:
    """A provider that omits usage (e.g. Ollama) yields zero tokens, no crash."""
    adapter = _build(_FakeClient(_make_response(omit_usage=True)))

    response = await adapter.complete(_request())

    assert response.input_tokens == 0
    assert response.output_tokens == 0
    assert response.cached is False


@pytest.mark.asyncio
async def test_cached_tokens_set_cached_flag() -> None:
    """cached reflects prompt_tokens_details.cached_tokens > 0 when present."""
    hit = _build(_FakeClient(_make_response(cached_tokens=4)))
    miss = _build(_FakeClient(_make_response(cached_tokens=0)))

    assert (await hit.complete(_request())).cached is True
    assert (await miss.complete(_request())).cached is False


@pytest.mark.asyncio
async def test_priced_model_estimates_cost() -> None:
    """A model in the price table gets the estimate_cost value."""
    adapter = _build(_FakeClient(_make_response(prompt_tokens=10, completion_tokens=5)))

    response = await adapter.complete(_request())

    # 10 * 1e-6 + 5 * 2e-6 == 2e-5
    assert response.cost_usd == pytest.approx(2e-5)


@pytest.mark.asyncio
async def test_unpriced_model_cost_is_none() -> None:
    """A model absent from the table reports cost None, not estimate_cost's 0.0."""
    adapter = _build(_FakeClient(_make_response()), model="deepseek-chat")

    response = await adapter.complete(_request(model="deepseek-chat"))

    assert response.cost_usd is None


@pytest.mark.asyncio
async def test_unpriced_model_warns_once_not_per_call() -> None:
    """Unpriced models are the recommended deepseek/minimax case — the
    'unpriced' warning must fire ONCE per model, not on every completion, so a
    scan run is not flooded (Decision 9: a miss is expected, not an error).
    """
    adapter = _build(_FakeClient(_make_response()), model="deepseek-chat")

    with capture_logs() as logs:
        await adapter.complete(_request(model="deepseek-chat"))
        await adapter.complete(_request(model="deepseek-chat"))

    unpriced = [e for e in logs if e.get("event") == "openai_compat_unpriced_model"]
    assert len(unpriced) == 1


@pytest.mark.asyncio
async def test_one_adapter_two_providers_by_base_url() -> None:
    """The same adapter class drives 2+ providers distinguished only by base_url."""
    openai_client = _FakeClient(
        _make_response("openai"), base_url="https://api.openai.com/v1"
    )
    deepseek_client = _FakeClient(
        _make_response("deepseek"), base_url="https://api.deepseek.com"
    )

    openai_adapter = _build(openai_client)
    deepseek_adapter = _build(deepseek_client, model="deepseek-chat")

    assert (await openai_adapter.complete(_request())).content == "openai"
    assert (
        await deepseek_adapter.complete(_request(model="deepseek-chat"))
    ).content == "deepseek"
    # No per-provider code path: both are OpenAiCompatLLM instances.
    assert type(openai_adapter) is type(deepseek_adapter)


def test_module_imports_without_openai_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the adapter and the factory import cleanly with the SDK absent.

    The `openai` import lives only under `TYPE_CHECKING` (adapter) and inside the
    factory's `openai-compat` branch, so neither module needs the SDK at import.
    """
    monkeypatch.setitem(sys.modules, "openai", None)
    for module_name in (
        "jobfeed.adapters.llm.openai_compat",
        "jobfeed.adapters.llm._factory",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
        reloaded = importlib.import_module(module_name)
        assert reloaded is not None

    assert hasattr(
        importlib.import_module("jobfeed.adapters.llm.openai_compat"),
        "OpenAiCompatLLM",
    )
    assert hasattr(
        importlib.import_module("jobfeed.adapters.llm._factory"),
        "build_llm_client",
    )
