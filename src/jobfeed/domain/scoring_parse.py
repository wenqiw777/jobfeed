"""Stage A and Stage B LLM response parsers (pure stdlib only).

Split from ``scoring.py`` to keep both modules within the 300-line gate.
Re-exported from ``jobfeed.domain.scoring`` so existing import paths work.
"""

from __future__ import annotations

import json
from typing import cast

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    MatchItem,
    StageAResult,
    StageBResult,
    Verdict,
)
from jobfeed.domain.types import VALID_SEVERITIES, Severity

JsonObject = dict[str, object]

MIN_FENCED_JSON_LINES = 2
_REFUSAL_PREFIXES = ("i cannot", "i'm sorry", "i apologize")
_REFUSAL_PHRASE = "as an ai"
_TRUNCATION_RATIO = 0.9
_DEFAULT_MAX_TOKENS = 4096
_MAP_SEVERITY: dict[str, str] = {
    "blocker": "critical",
    "notable": "major",
    "minor": "minor",
}


def parse_stage_a_response(
    raw: str,
    model: str,
    prompt_hash: str,
    resume_hash: str,
    cost_usd: float | None = None,
) -> StageAResult:
    """Parse and validate the canonical Stage A raw LLM response.

    Args:
        raw: Raw LLM response, optionally wrapped in Markdown JSON fence.
        model: Model identifier supplied by the caller.
        prompt_hash: Prompt content hash supplied by the service layer.
        resume_hash: Resume content hash supplied by the service layer.
        cost_usd: LLM cost supplied by the adapter, or None.

    Returns:
        Normalized Stage A result.

    Raises:
        ScoringParseError: If the response is not valid Stage A JSON.
    """
    _detect_refusal(raw)
    try:
        parsed = _parse_json_object(raw)
        return StageAResult(
            score=_require_int(parsed, "score"),
            one_line=_require_string(parsed, "one_line"),
            timing_eligible=_require_string(parsed, "timing_eligible"),
            model=model,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
            cost_usd=cost_usd,
        )
    except ScoringParseError as exc:
        exc.raw_response = exc.raw_response or raw
        _detect_truncation(raw)
        raise
    except ValueError as exc:
        raise ScoringParseError(str(exc), raw_response=raw) from exc


def parse_stage_b_response(
    raw: str,
    model: str,
    prompt_hash: str,
    resume_hash: str,
    cost_usd: float | None = None,
) -> StageBResult:
    """Parse and validate the Stage B raw LLM response (legacy schema).

    Args:
        raw: Raw LLM response, optionally wrapped in Markdown JSON fence.
        model: Model identifier supplied by the caller.
        prompt_hash: Prompt content hash supplied by the service layer.
        resume_hash: Resume content hash supplied by the service layer.
        cost_usd: LLM cost supplied by the adapter, or None.

    Returns:
        Normalized Stage B result with raw block JSON preserved.

    Raises:
        ScoringParseError: If any required Stage B block or field is invalid.
    """
    _detect_refusal(raw)
    try:
        parsed = _parse_json_object(raw)
        verdict_block = _require_object(parsed, "verdict")
        verdict = Verdict(_require_string(verdict_block, "recommendation"))
        jd_summary = _flatten_jd_summary(_require_object(parsed, "jd_summary"))
        fit_block = _require_object(parsed, "fit_analysis")
        strengths = [
            _build_match_item(i) for i in _require_list(fit_block, "strong_match")
        ]
        gaps = [_build_gap_item(i) for i in _require_list(fit_block, "gaps")]
        hooks_block = _require_object(parsed, "resume_hooks")
        return StageBResult(
            verdict=verdict,
            jd_summary=jd_summary,
            fit_analysis=FitAnalysis(
                score=_require_int(fit_block, "score_0_100"),
                strengths=strengths,
                gaps=gaps,
            ),
            resume_hooks=_build_hooks(hooks_block),
            model=model,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
            cost_usd=cost_usd,
            raw_blocks=parsed,
        )
    except ScoringParseError as exc:
        exc.raw_response = exc.raw_response or raw
        raise
    except ValueError as exc:
        raise ScoringParseError(str(exc), raw_response=raw) from exc


# -- safety checks --


def _detect_refusal(raw: str) -> None:
    """Raise ScoringParseError if the response looks like an LLM refusal."""
    lower = raw.strip().lower()
    for prefix in _REFUSAL_PREFIXES:
        if lower.startswith(prefix):
            raise ScoringParseError("LLM refusal", raw_response=raw)
    if _REFUSAL_PHRASE in lower:
        raise ScoringParseError("LLM refusal", raw_response=raw)


def _detect_truncation(raw: str) -> None:
    """Raise ScoringParseError if the response appears truncated."""
    if len(raw) > _DEFAULT_MAX_TOKENS * _TRUNCATION_RATIO:
        raise ScoringParseError("response likely truncated", raw_response=raw)
    if _has_unbalanced_braces(raw):
        raise ScoringParseError(
            "incomplete JSON -- possible token limit", raw_response=raw
        )


def _has_unbalanced_braces(raw: str) -> bool:
    """Check whether JSON braces are unbalanced."""
    depth = 0
    for ch in raw:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth > 0


# -- JSON extraction --


def _parse_json_object(raw: str) -> JsonObject:
    """Parse raw text as a JSON object, stripping markdown fences."""
    stripped = _strip_markdown_json(raw)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ScoringParseError("response is not valid JSON", raw_response=raw) from exc
    if not isinstance(parsed, dict):
        raise ScoringParseError("response must be a JSON object")
    return cast(JsonObject, parsed)


def _strip_markdown_json(raw: str) -> str:
    """Strip markdown JSON fence from a raw response."""
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < MIN_FENCED_JSON_LINES or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _require_object(data: JsonObject, key: str) -> JsonObject:
    """Extract a required JSON object field."""
    value = data.get(key)
    if not isinstance(value, dict):
        raise ScoringParseError(f"missing or invalid object: {key}")
    return cast(JsonObject, value)


def _require_list(data: JsonObject, key: str) -> list[object]:
    """Extract a required JSON list field."""
    value = data.get(key)
    if not isinstance(value, list):
        raise ScoringParseError(f"missing or invalid list: {key}")
    return cast(list[object], value)


def _require_string(data: JsonObject, key: str) -> str:
    """Extract a required non-empty JSON string field."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ScoringParseError(f"missing or invalid string: {key}")
    return value


def _require_int(data: JsonObject, key: str) -> int:
    """Extract a required JSON integer field."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoringParseError(f"missing or invalid integer: {key}")
    return value


# -- legacy schema builders --


def _flatten_jd_summary(jd_block: JsonObject) -> str:
    """Flatten a structured jd_summary block to a plain-text string."""
    role = _require_string(jd_block, "role_in_3_lines")
    parts = [role]
    for label, key in [
        ("Must-haves", "must_haves"),
        ("Nice-to-haves", "nice_to_haves"),
        ("Red flags", "red_flags_in_jd"),
    ]:
        items = jd_block.get(key, [])
        if isinstance(items, list) and items:
            parts.append(f"{label}: " + ", ".join(str(x) for x in items))
    return "\n\n".join(parts)


def _build_match_item(item: object) -> MatchItem:
    """Build a MatchItem from legacy evidence_from_resume key."""
    if not isinstance(item, dict):
        raise ScoringParseError("invalid object item in strong_match")
    obj = cast(JsonObject, item)
    return MatchItem(
        requirement=_require_string(obj, "requirement"),
        evidence=_require_string(obj, "evidence_from_resume"),
    )


def _build_gap_item(item: object) -> GapItem:
    """Build a GapItem with legacy severity mapping."""
    if not isinstance(item, dict):
        raise ScoringParseError("invalid object item in gaps")
    obj = cast(JsonObject, item)
    raw_sev = _require_string(obj, "severity")
    mapped = _MAP_SEVERITY.get(raw_sev, raw_sev)
    if mapped not in VALID_SEVERITIES:
        raise ScoringParseError("invalid gap severity")
    mitigation = obj.get("mitigation")
    if mitigation is None:
        mit_str = ""
    elif not isinstance(mitigation, str):
        raise ScoringParseError("invalid string: mitigation")
    else:
        mit_str = mitigation
    return GapItem(
        requirement=_require_string(obj, "requirement"),
        severity=cast(Severity, mapped),
        mitigation=mit_str,
    )


def _build_hooks(hooks_block: JsonObject) -> list[str]:
    """Build resume hooks list from legacy lead_with + supporting."""
    lead = _require_string(hooks_block, "lead_with")
    supporting = hooks_block.get("supporting", [])
    if not isinstance(supporting, list):
        raise ScoringParseError("missing or invalid list: supporting")
    result = [lead]
    for item in supporting:
        if not isinstance(item, str) or not item:
            raise ScoringParseError("invalid string item in supporting")
        result.append(item)
    return result


__all__ = ["parse_stage_a_response", "parse_stage_b_response"]
