"""Shared JSON-shape accessors for the scoring parsers (pure stdlib).

These primitives (``JsonObject`` plus the ``_require_*`` accessors) are used by
both ``scoring_parse`` (value coercion when building domain dataclasses) and
``scoring_schema`` (strict shape validation). They live here as the single
source of truth for the JSON access contract, replacing the prior verbatim
copy in each module.
"""

from __future__ import annotations

from typing import cast

from jobfeed.domain.errors import ScoringParseError

JsonObject = dict[str, object]


def _require_object(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ScoringParseError(f"missing or invalid object: {key}")
    return cast(JsonObject, value)


def _require_list(data: JsonObject, key: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ScoringParseError(f"missing or invalid list: {key}")
    return cast(list[object], value)


def _require_string(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ScoringParseError(f"missing or invalid string: {key}")
    return value
