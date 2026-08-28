"""Unit tests for the price-table filter script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/update_prices.py"
_spec = importlib.util.spec_from_file_location("update_prices", _SCRIPT)
assert _spec is not None and _spec.loader is not None
update_prices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update_prices)


def test_filter_openai_keeps_only_openai_provider() -> None:
    """Only entries with litellm_provider == 'openai' survive."""
    raw = {
        "gpt-5.4-mini": {"litellm_provider": "openai", "input_cost_per_token": 1e-06},
        "claude-haiku-4-5": {"litellm_provider": "anthropic"},
        "gemini-pro": {"litellm_provider": "gemini"},
        "gpt-5.5": {"litellm_provider": "openai"},
    }

    result = update_prices.filter_openai(raw)

    assert set(result) == {"gpt-5.4-mini", "gpt-5.5"}


def test_filter_openai_drops_non_dict_entries() -> None:
    """Schema sample / scalar entries without a provider are dropped."""
    raw = {
        "sample_spec": "not-a-dict",
        "gpt-5.5": {"litellm_provider": "openai"},
    }

    result = update_prices.filter_openai(raw)

    assert set(result) == {"gpt-5.5"}


def test_vendored_table_contains_only_openai_and_pinned_bedrock() -> None:
    """The table stays limited to OpenAI plus the pinned Bedrock models."""
    table_path = (
        Path(__file__).resolve().parents[2]
        / "src/jobfeed/adapters/llm/model_prices.json"
    )
    data = json.loads(table_path.read_text())

    non_openai = {
        key
        for key, value in data.items()
        if isinstance(value, dict) and value.get("litellm_provider") != "openai"
    }
    assert non_openai == set(update_prices.BEDROCK_PRICES)
