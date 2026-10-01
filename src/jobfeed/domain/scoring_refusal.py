"""Refusal and truncation heuristics for scoring response parsers."""

from __future__ import annotations

from jobfeed.domain.errors import ScoringParseError

_REFUSAL_PREFIXES = ("i cannot", "i'm sorry", "i apologize")
_REFUSAL_PHRASES = ("as an ai assistant", "as an ai language model")


def _detect_refusal(raw: str) -> None:
    lower = raw.strip().lower()
    has_prefix = any(lower.startswith(prefix) for prefix in _REFUSAL_PREFIXES)
    has_phrase = any(phrase in lower for phrase in _REFUSAL_PHRASES)
    if has_prefix or has_phrase:
        raise ScoringParseError("LLM refusal", raw_response=raw)


def _detect_refusal_fields(value: object) -> None:
    if isinstance(value, str):
        _detect_structured_refusal(value)
    elif isinstance(value, dict):
        for item in value.values():
            _detect_refusal_fields(item)
    elif isinstance(value, list):
        for item in value:
            _detect_refusal_fields(item)


def _detect_structured_refusal(raw: str) -> None:
    lower = raw.strip().lower()
    has_prefix = any(lower.startswith(prefix) for prefix in _REFUSAL_PREFIXES)
    has_identity = any(phrase in lower for phrase in _REFUSAL_PHRASES)
    has_refusal = any(term in lower for term in ("cannot", "can't", "unable"))
    if has_prefix or (has_identity and has_refusal):
        raise ScoringParseError("LLM refusal", raw_response=raw)


def _detect_truncation(raw: str) -> None:
    if not raw.rstrip().endswith("}") and _has_unbalanced_braces(raw):
        raise ScoringParseError(
            "incomplete JSON -- possible token limit", raw_response=raw
        )


def _has_unbalanced_braces(raw: str) -> bool:
    depth = 0
    in_string = escaped = False
    for ch in raw:
        if escaped:
            escaped = False
            continue
        if in_string and ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth > 0
