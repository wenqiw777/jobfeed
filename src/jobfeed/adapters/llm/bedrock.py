"""Native Amazon Bedrock Converse adapter."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from jobfeed.adapters.llm._pricing import ModelPricing, TokenUsage, estimate_cost
from jobfeed.domain.models import LLMRequest, LLMResponse
from jobfeed.observability import JobfeedLogger

_MS_PER_S = 1000
_INFERENCE_PROFILE_PREFIXES = ("global.", "us.", "eu.", "au.", "jp.", "apac.")


class BedrockRuntimeClient(Protocol):
    """Synchronous subset of the Boto3 Bedrock Runtime client we use."""

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        """Invoke one Bedrock conversational model.

        Args:
            **kwargs: Boto3 Converse request fields.

        Returns:
            Raw Boto3 Converse response.
        """
        ...


class BedrockLLM:
    """LLM client backed by Amazon Bedrock's provider-neutral Converse API."""

    def __init__(
        self,
        *,
        client: BedrockRuntimeClient,
        model: str,
        region: str,
        price_table: dict[str, ModelPricing],
        logger: JobfeedLogger,
    ) -> None:
        self._client = client
        self._model = model
        self._region = region
        self._price_table = price_table
        self._logger = logger
        self._pricing_model = _require_pricing_model(model, price_table)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Invoke Converse without blocking Jobfeed's async worker loop.

        Args:
            request: Adapter-neutral completion request.

        Returns:
            Normalized content, usage, latency, and estimated cost.
        """
        system = [
            {"text": message.content}
            for message in request.messages
            if message.role == "system"
        ]
        messages = [
            {
                "role": message.role,
                "content": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        payload: dict[str, Any] = {
            "modelId": self._model,
            "messages": messages,
            "inferenceConfig": {
                "temperature": request.temperature,
                "maxTokens": request.max_tokens,
            },
        }
        if system:
            payload["system"] = system

        start = time.monotonic()
        response = await asyncio.to_thread(self._client.converse, **payload)
        latency_ms = int((time.monotonic() - start) * _MS_PER_S)
        content = _extract_content(response)
        input_tokens, output_tokens, cached_input_tokens = _extract_usage(response)
        cost = estimate_cost(
            self._pricing_model,
            TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            ),
            price_table=self._price_table,
        )
        request_id = _request_id(response)
        self._logger.info(
            "bedrock_completion",
            aws_request_id=request_id,
            model=self._model,
            region=self._region,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
        )
        return LLMResponse(
            content=content,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cached=cached_input_tokens > 0,
            latency_ms=latency_ms,
        )


def _require_pricing_model(model: str, price_table: dict[str, ModelPricing]) -> str:
    if model in price_table:
        return model
    for prefix in _INFERENCE_PROFILE_PREFIXES:
        if model.startswith(prefix) and model.removeprefix(prefix) in price_table:
            return model.removeprefix(prefix)
    raise ValueError(
        f"bedrock model {model!r} has no vendored pricing. Add verified pricing "
        "or choose a priced model before making real paid calls."
    )


def _extract_content(response: dict[str, Any]) -> str:
    output = response.get("output")
    message = output.get("message") if isinstance(output, dict) else None
    blocks = message.get("content") if isinstance(message, dict) else None
    if not isinstance(blocks, list):
        return ""
    return "".join(
        text
        for block in blocks
        if isinstance(block, dict) and isinstance((text := block.get("text")), str)
    )


def _extract_usage(response: dict[str, Any]) -> tuple[int, int, int]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0
    return (
        _non_negative_int(usage.get("inputTokens")),
        _non_negative_int(usage.get("outputTokens")),
        _non_negative_int(usage.get("cacheReadInputTokens")),
    )


def _request_id(response: dict[str, Any]) -> str | None:
    metadata = response.get("ResponseMetadata")
    value = metadata.get("RequestId") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else None


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


__all__ = ["BedrockLLM", "BedrockRuntimeClient"]
