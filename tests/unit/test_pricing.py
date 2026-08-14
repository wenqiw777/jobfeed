"""Unit tests for LLM model pricing lookup and cost estimation."""

from __future__ import annotations

import logging

import pytest

from jobfeed.adapters.llm._pricing import (
    ModelPricing,
    TokenUsage,
    estimate_cost,
    load_price_table,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_MODEL = "gpt-5.4-mini"
CURRENT_CODEX_MODELS = {
    "gpt-5.6-sol": (5e-6, 30e-6),
    "gpt-5.6-terra": (2e-6, 12e-6),
    "gpt-5.6-luna": (0.2e-6, 1.2e-6),
}
UNKNOWN_MODEL = "nonexistent/model-xyz-99"

TOKEN_COUNT_1K = 1_000
TOKEN_COUNT_500 = 500
TOKEN_COUNT_200 = 200


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def price_table() -> dict[str, ModelPricing]:
    """Load the vendored price table once per module."""
    return load_price_table()


# ---------------------------------------------------------------------------
# load_price_table
# ---------------------------------------------------------------------------


def test_load_price_table_returns_nonempty_dict(
    price_table: dict[str, ModelPricing],
) -> None:
    """Vendored JSON should parse into a non-empty model pricing table."""
    assert len(price_table) > 0


def test_load_price_table_contains_known_model(
    price_table: dict[str, ModelPricing],
) -> None:
    """Table should contain a well-known OpenAI model."""
    assert KNOWN_MODEL in price_table


def test_load_price_table_contains_current_codex_models(
    price_table: dict[str, ModelPricing],
) -> None:
    """Every model offered by Settings must have exact vendored pricing."""
    for model, (input_rate, output_rate) in CURRENT_CODEX_MODELS.items():
        assert price_table[model].input_cost_per_token == pytest.approx(input_rate)
        assert price_table[model].output_cost_per_token == pytest.approx(output_rate)


def test_load_price_table_entries_are_model_pricing(
    price_table: dict[str, ModelPricing],
) -> None:
    """Every value in the table should be a ModelPricing instance."""
    sample = price_table[KNOWN_MODEL]
    assert isinstance(sample, ModelPricing)
    assert sample.input_cost_per_token > 0
    assert sample.output_cost_per_token > 0


# ---------------------------------------------------------------------------
# estimate_cost — known model
# ---------------------------------------------------------------------------


def test_estimate_cost_known_model_returns_positive(
    price_table: dict[str, ModelPricing],
) -> None:
    """Cost for a known model with non-zero tokens should be positive."""
    cost = estimate_cost(
        KNOWN_MODEL,
        TokenUsage(
            input_tokens=TOKEN_COUNT_1K,
            output_tokens=TOKEN_COUNT_500,
        ),
        price_table=price_table,
    )
    assert cost > 0.0


# ---------------------------------------------------------------------------
# estimate_cost — unknown model
# ---------------------------------------------------------------------------


def test_estimate_cost_unknown_model_returns_zero(
    price_table: dict[str, ModelPricing],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown model should return 0.0 and log a warning."""
    with caplog.at_level(logging.WARNING):
        cost = estimate_cost(
            UNKNOWN_MODEL,
            TokenUsage(
                input_tokens=TOKEN_COUNT_1K,
                output_tokens=TOKEN_COUNT_500,
            ),
            price_table=price_table,
        )
    assert cost == 0.0
    assert UNKNOWN_MODEL in caplog.text


# ---------------------------------------------------------------------------
# estimate_cost — cached input tokens
# ---------------------------------------------------------------------------


def test_estimate_cost_cached_tokens_cheaper_than_uncached(
    price_table: dict[str, ModelPricing],
) -> None:
    """Cached input tokens should cost less than the same count uncached."""
    pricing = price_table[KNOWN_MODEL]
    if pricing.cached_input_cost_per_token is None:
        pytest.skip("known model has no cached pricing")

    cost_uncached = estimate_cost(
        KNOWN_MODEL,
        TokenUsage(input_tokens=TOKEN_COUNT_1K, output_tokens=0),
        price_table=price_table,
    )
    cost_cached = estimate_cost(
        KNOWN_MODEL,
        TokenUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=TOKEN_COUNT_1K,
        ),
        price_table=price_table,
    )
    assert cost_cached < cost_uncached


def test_estimate_cost_cached_fallback_when_no_cached_rate() -> None:
    """When cached rate is None, cached tokens use input rate."""
    table = {
        "test-model": ModelPricing(
            input_cost_per_token=0.01,
            output_cost_per_token=0.02,
            cached_input_cost_per_token=None,
        ),
    }
    cost = estimate_cost(
        "test-model",
        TokenUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=TOKEN_COUNT_1K,
        ),
        price_table=table,
    )
    expected = TOKEN_COUNT_1K * 0.01
    assert cost == pytest.approx(expected)


def test_estimate_cost_cached_tokens_not_charged_twice() -> None:
    """Cached tokens included in input_tokens should use only the cached rate."""
    table = {
        "test-model": ModelPricing(
            input_cost_per_token=0.01,
            output_cost_per_token=0.02,
            cached_input_cost_per_token=0.001,
        ),
    }

    cost = estimate_cost(
        "test-model",
        TokenUsage(
            input_tokens=TOKEN_COUNT_1K,
            output_tokens=0,
            cached_input_tokens=TOKEN_COUNT_200,
        ),
        price_table=table,
    )

    expected = (TOKEN_COUNT_1K - TOKEN_COUNT_200) * 0.01
    expected += TOKEN_COUNT_200 * 0.001
    assert cost == pytest.approx(expected)


# ---------------------------------------------------------------------------
# estimate_cost — reasoning output tokens
# ---------------------------------------------------------------------------


def test_estimate_cost_reasoning_tokens_use_dedicated_rate() -> None:
    """Reasoning tokens should use reasoning_output_cost_per_token."""
    reasoning_rate = 0.05
    output_rate = 0.02
    table = {
        "test-model": ModelPricing(
            input_cost_per_token=0.01,
            output_cost_per_token=output_rate,
            reasoning_output_cost_per_token=reasoning_rate,
        ),
    }
    cost = estimate_cost(
        "test-model",
        TokenUsage(
            input_tokens=0,
            output_tokens=0,
            reasoning_output_tokens=TOKEN_COUNT_200,
        ),
        price_table=table,
    )
    expected = TOKEN_COUNT_200 * reasoning_rate
    assert cost == pytest.approx(expected)


def test_estimate_cost_reasoning_fallback_when_no_reasoning_rate() -> None:
    """When reasoning rate is None, reasoning tokens use output rate."""
    output_rate = 0.02
    table = {
        "test-model": ModelPricing(
            input_cost_per_token=0.01,
            output_cost_per_token=output_rate,
            reasoning_output_cost_per_token=None,
        ),
    }
    cost = estimate_cost(
        "test-model",
        TokenUsage(
            input_tokens=0,
            output_tokens=0,
            reasoning_output_tokens=TOKEN_COUNT_200,
        ),
        price_table=table,
    )
    expected = TOKEN_COUNT_200 * output_rate
    assert cost == pytest.approx(expected)


# ---------------------------------------------------------------------------
# ModelPricing defaults
# ---------------------------------------------------------------------------


def test_model_pricing_defaults() -> None:
    """Optional fields should default to None."""
    pricing = ModelPricing(
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
    )
    assert pricing.cached_input_cost_per_token is None
    assert pricing.reasoning_output_cost_per_token is None
