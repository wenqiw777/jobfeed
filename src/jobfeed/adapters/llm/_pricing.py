"""LLM model pricing lookup and cost estimation from vendored LiteLLM data."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PRICES_FILENAME = "model_prices.json"


@dataclass
class ModelPricing:
    """Per-token pricing for a single LLM model.

    Attributes:
        input_cost_per_token: USD cost per input token.
        output_cost_per_token: USD cost per output token.
        cached_input_cost_per_token: USD cost per cached input token, if the
            provider offers a cache discount.  Falls back to
            ``input_cost_per_token`` when ``None``.
        reasoning_output_cost_per_token: USD cost per reasoning output token,
            if the model separates reasoning tokens from regular output.
            Falls back to ``output_cost_per_token`` when ``None``.
    """

    input_cost_per_token: float
    output_cost_per_token: float
    cached_input_cost_per_token: float | None = None
    reasoning_output_cost_per_token: float | None = None


def load_price_table(path: Path | None = None) -> dict[str, ModelPricing]:
    """Load the vendored LiteLLM price table.

    Args:
        path: Explicit path to the JSON file.  Defaults to
            ``model_prices.json`` in this package directory.

    Returns:
        Mapping of model name to ``ModelPricing``.
    """
    if path is None:
        path = Path(__file__).parent / _PRICES_FILENAME

    raw: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))

    table: dict[str, ModelPricing] = {}
    for model_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        input_cost = entry.get("input_cost_per_token")
        output_cost = entry.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            continue
        table[model_name] = ModelPricing(
            input_cost_per_token=float(input_cost),
            output_cost_per_token=float(output_cost),
            cached_input_cost_per_token=_opt_float(
                entry.get("cache_read_input_token_cost")
            ),
            reasoning_output_cost_per_token=_opt_float(
                entry.get("output_cost_per_reasoning_token")
            ),
        )
    return table


def estimate_cost(  # noqa: PLR0913
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    *,
    price_table: dict[str, ModelPricing],
) -> float:
    """Estimate USD cost from token counts.

    Args:
        model: Model identifier matching a key in *price_table*.
        input_tokens: Number of non-cached input tokens.
        output_tokens: Number of non-reasoning output tokens.
        cached_input_tokens: Input tokens served from the provider cache.
        reasoning_output_tokens: Output tokens classified as reasoning.
        price_table: Pre-loaded model pricing table (from ``load_price_table``).

    Returns:
        Estimated cost in USD.  Returns ``0.0`` with a logged warning when
        *model* is not present in *price_table*.
    """
    pricing = price_table.get(model)
    if pricing is None:
        logger.warning("Model %r not found in price table; cost set to $0.00", model)
        return 0.0

    cached_rate = (
        pricing.cached_input_cost_per_token
        if pricing.cached_input_cost_per_token is not None
        else pricing.input_cost_per_token
    )
    reasoning_rate = (
        pricing.reasoning_output_cost_per_token
        if pricing.reasoning_output_cost_per_token is not None
        else pricing.output_cost_per_token
    )

    return (
        input_tokens * pricing.input_cost_per_token
        + output_tokens * pricing.output_cost_per_token
        + cached_input_tokens * cached_rate
        + reasoning_output_tokens * reasoning_rate
    )


def _opt_float(value: object) -> float | None:
    """Convert a nullable numeric value to float or None."""
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


__all__ = ["ModelPricing", "estimate_cost", "load_price_table"]
