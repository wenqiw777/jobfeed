#!/usr/bin/env python3
"""Filter the LiteLLM price dump down to OpenAI models.

Reads the full ``model_prices_and_context_window.json`` from stdin and writes
only ``litellm_provider == "openai"`` entries to the vendored price table.
Phase 3's Codex backend is the sole consumer of the table (Claude returns
exact cost in its envelope), so shipping every provider's models is dead
weight. Output is sorted and indented for a stable, review-friendly diff.

Usage (see ``make update-prices``):
    curl -sL <litellm-url> | python3 scripts/update_prices.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT = _REPO_ROOT / "src/jobfeed/adapters/llm/model_prices.json"
_PROVIDER = "openai"


def filter_openai(raw: dict[str, object]) -> dict[str, object]:
    """Return only entries whose ``litellm_provider`` is OpenAI."""
    return {
        key: value
        for key, value in raw.items()
        if isinstance(value, dict) and value.get("litellm_provider") == _PROVIDER
    }


def main() -> int:
    raw = json.load(sys.stdin)
    if not isinstance(raw, dict):
        print("expected a JSON object from stdin", file=sys.stderr)
        return 1
    filtered = filter_openai(raw)
    if not filtered:
        print("no OpenAI models found — refusing to write empty table", file=sys.stderr)
        return 1
    _OUTPUT.write_text(json.dumps(filtered, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(filtered)} OpenAI models to {_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
