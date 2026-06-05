"""OpenAiCompatLLM adapter — one LLM client for all OpenAI-compatible APIs.

A single adapter (Decision 9) drives OpenAI, DeepSeek, MiniMax, OpenRouter,
Together, Groq, and local Ollama/vLLM/LM Studio — parameterised entirely by
``base_url`` + API-key env name + model, configured on the injected SDK client.

The ``openai`` SDK is imported ONLY under ``TYPE_CHECKING`` here, so this module
imports cleanly with the SDK absent.  The concrete ``AsyncOpenAI`` client is
built by the factory and injected, which keeps this class ≤5 args, SDK-free at
import time, and trivially mockable with a fake client.

Wire contract is the lowest common denominator (Decision 9): only the
``system``/``user``/``assistant`` roles and core sampling params are sent. No
``response_format``/``json_schema`` (would break LCD portability), no
vendor-specific fields.  The ``openai`` SDK and its types never escape this
module.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from jobfeed.adapters.llm._pricing import ModelPricing, TokenUsage, estimate_cost
from jobfeed.domain.models import LLMRequest, LLMResponse
from jobfeed.observability import JobfeedLogger

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_MS_PER_S = 1000


class OpenAiCompatLLM:
    """LLMClient adapter for any OpenAI-compatible ``/chat/completions`` API.

    Takes an injected async ``AsyncOpenAI`` client whose ``base_url``, API key,
    timeout, and retries are already configured by the factory.  Cost is
    best-effort: priced models estimate from the vendored table, unpriced
    models report ``cost_usd=None`` (distinguishable from a genuine zero).
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        price_table: dict[str, ModelPricing],
        logger: JobfeedLogger,
    ) -> None:
        self._client = client
        self._model = model
        self._price_table = price_table
        self._logger = logger
        self._warned_unpriced: set[str] = set()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run an OpenAI-compatible chat completion.

        Args:
            request: Adapter-neutral completion request with messages.

        Returns:
            Parsed LLM response with content, usage, and best-effort cost.
        """
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]

        start = time.monotonic()
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        latency_ms = int((time.monotonic() - start) * _MS_PER_S)

        return self._build_response(self._model, response, latency_ms)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_response(
        self,
        model: str,
        response: object,
        latency_ms: int,
    ) -> LLMResponse:
        """Construct an ``LLMResponse`` from the SDK response object."""
        content = _extract_content(response)
        input_tokens, output_tokens, cached_input_tokens = _extract_usage(response)
        cost = self._estimate_cost(
            model, input_tokens, output_tokens, cached_input_tokens
        )

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cached=cached_input_tokens > 0,
            latency_ms=latency_ms,
        )

    def _estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
    ) -> float | None:
        """Best-effort cost: ``None`` when the model is absent from the table.

        Guards the price-table miss BEFORE calling ``estimate_cost`` so an
        unknown cost is reported as ``None`` rather than ``estimate_cost``'s
        ``0.0``-on-miss (which would misrepresent unknown cost as free and fool
        the dollar budget gate).
        """
        if model not in self._price_table:
            if model not in self._warned_unpriced:
                # Expected for the recommended non-OpenAI providers (deepseek,
                # minimax, local) — Decision 9. Warn ONCE per model, not per
                # call, so a scan run is not flooded.
                self._logger.warning(
                    "openai_compat_unpriced_model",
                    model=model,
                )
                self._warned_unpriced.add(model)
            return None
        return estimate_cost(
            model,
            TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            ),
            price_table=self._price_table,
        )


def _extract_content(response: object) -> str:
    """Pull ``choices[0].message.content`` off the SDK response."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def _extract_usage(response: object) -> tuple[int, int, int]:
    """Parse usage tokens, guarding omitted/None usage with ``0``.

    Returns ``(input_tokens, output_tokens, cached_input_tokens)``. Some
    providers (e.g. local Ollama) omit ``usage`` entirely. The cached count is
    best-effort from ``usage.prompt_tokens_details.cached_tokens`` when present,
    and is passed to cost estimation so cached prompt tokens are billed at the
    discounted cache-read rate rather than the full input rate.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0

    input_tokens = _coerce_int(getattr(usage, "prompt_tokens", 0))
    output_tokens = _coerce_int(getattr(usage, "completion_tokens", 0))
    cached_input_tokens = _extract_cached_tokens(usage)
    return input_tokens, output_tokens, cached_input_tokens


def _extract_cached_tokens(usage: object) -> int:
    """Best-effort cached prompt-token count from ``prompt_tokens_details``."""
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    return _coerce_int(getattr(details, "cached_tokens", 0))


def _coerce_int(value: object) -> int:
    """Coerce a possibly-None numeric token count to ``int`` (None → 0)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


__all__ = ["OpenAiCompatLLM"]
