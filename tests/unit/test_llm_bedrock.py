"""Amazon Bedrock adapter tests with a fake synchronous Boto3 client."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from jobfeed.adapters.llm._pricing import ModelPricing
from jobfeed.adapters.llm.bedrock import BedrockLLM
from jobfeed.domain.models import LLMRequest, Message
from jobfeed.observability import get_logger
from jobfeed.ports.llm import LLMClient

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
PRICING_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
INPUT_TOKENS = 12
OUTPUT_TOKENS = 7


class _FakeBedrockRuntime:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self._response


def _response() -> dict[str, Any]:
    return {
        "output": {"message": {"content": [{"text": '{"score": 91}'}]}},
        "usage": {
            "inputTokens": INPUT_TOKENS,
            "outputTokens": OUTPUT_TOKENS,
            "cacheReadInputTokens": 4,
        },
        "ResponseMetadata": {"RequestId": "bedrock-request-123"},
    }


def _request() -> LLMRequest:
    return LLMRequest(
        model=MODEL,
        messages=[
            Message(role="system", content="Return JSON only."),
            Message(role="user", content="Evaluate this job."),
            Message(role="assistant", content="Previous answer."),
        ],
        temperature=0.2,
        max_tokens=321,
    )


@pytest.mark.asyncio
async def test_converse_maps_messages_usage_cost_and_request_id_log() -> None:
    client = _FakeBedrockRuntime(_response())
    adapter = BedrockLLM(
        client=client,
        model=MODEL,
        region="us-east-1",
        price_table={
            PRICING_MODEL: ModelPricing(
                input_cost_per_token=1.1e-6,
                output_cost_per_token=5.5e-6,
                cached_input_cost_per_token=0.11e-6,
            )
        },
        logger=get_logger(),
    )

    assert isinstance(adapter, LLMClient)
    with capture_logs() as logs:
        result = await adapter.complete(_request())

    assert client.calls == [
        {
            "modelId": MODEL,
            "system": [{"text": "Return JSON only."}],
            "messages": [
                {"role": "user", "content": [{"text": "Evaluate this job."}]},
                {
                    "role": "assistant",
                    "content": [{"text": "Previous answer."}],
                },
            ],
            "inferenceConfig": {"temperature": 0.2, "maxTokens": 321},
        }
    ]
    assert result.content == '{"score": 91}'
    assert result.model == MODEL
    assert result.input_tokens == INPUT_TOKENS
    assert result.output_tokens == OUTPUT_TOKENS
    assert result.cached is True
    assert result.cost_usd == pytest.approx(4 * 0.11e-6 + 8 * 1.1e-6 + 7 * 5.5e-6)
    assert result.latency_ms >= 0
    assert any(
        event.get("event") == "bedrock_completion"
        and event.get("aws_request_id") == "bedrock-request-123"
        and event.get("region") == "us-east-1"
        for event in logs
    )


def test_unpriced_model_fails_before_any_paid_call() -> None:
    client = _FakeBedrockRuntime(_response())

    with pytest.raises(ValueError, match="no vendored pricing"):
        BedrockLLM(
            client=client,
            model="arn:aws:bedrock:us-east-1:123:inference-profile/custom",
            region="us-east-1",
            price_table={},
            logger=get_logger(),
        )

    assert client.calls == []
