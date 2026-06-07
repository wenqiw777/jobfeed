"""Stage A and Stage B LLM response parsers (pure stdlib only)."""

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
from jobfeed.domain.scoring_json import (
    JsonObject,
    _require_list,
    _require_object,
    _require_string,
)
from jobfeed.domain.scoring_refusal import (
    _detect_refusal,
    _detect_refusal_fields,
    _detect_truncation,
)
from jobfeed.domain.scoring_schema import (
    _validate_stage_a_shape,
    _validate_stage_b_shape,
)
from jobfeed.domain.types import VALID_SEVERITIES, Severity

MIN_FENCED_JSON_LINES = 2
_VALID_TIMING_ELIGIBLE = frozenset(
    {"eligible", "mismatch", "unclear", "maybe", "ineligible"}
)
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
    try:
        parsed = _parse_or_refusal(raw, detect_truncation=True)
        _validate_stage_a_shape(parsed)
        _detect_refusal_fields(parsed)
        return StageAResult(
            score=_require_int(parsed, "score"),
            one_line=_require_string(parsed, "one_line"),
            timing_eligible=_require_timing_eligible(parsed),
            model=model,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
            cost_usd=cost_usd,
        )
    except ScoringParseError as exc:
        exc.raw_response = exc.raw_response or raw
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
    try:
        parsed = _parse_or_refusal(raw, detect_truncation=True)
        _validate_stage_b_shape(parsed)
        _detect_refusal_fields(parsed)
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


def _parse_or_refusal(raw: str, *, detect_truncation: bool) -> JsonObject:
    try:
        return _parse_json_object(raw)
    except ScoringParseError as exc:
        _detect_refusal(raw)
        exc.raw_response = exc.raw_response or raw
        if detect_truncation:
            _detect_truncation(raw)
        raise


def _parse_json_object(raw: str) -> JsonObject:
    stripped = _strip_markdown_json(raw)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ScoringParseError("response is not valid JSON", raw_response=raw) from exc
    if not isinstance(parsed, dict):
        raise ScoringParseError("response must be a JSON object")
    return cast(JsonObject, parsed)


def _strip_markdown_json(raw: str) -> str:
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < MIN_FENCED_JSON_LINES or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _require_int(data: JsonObject, key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoringParseError(f"missing or invalid integer: {key}")
    return value


def _require_timing_eligible(data: JsonObject) -> str:
    value = _require_string(data, "timing_eligible")
    if value not in _VALID_TIMING_ELIGIBLE:
        raise ScoringParseError("invalid timing_eligible")
    return value


def _require_string_list(data: JsonObject, key: str) -> list[str]:
    items = _require_list(data, key)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise ScoringParseError(f"invalid string item in {key}")
        result.append(item)
    return result


def _flatten_jd_summary(jd_block: JsonObject) -> str:
    role = _require_string(jd_block, "role_in_3_lines")
    parts = [role]
    for label, key in [
        ("Must-haves", "must_haves"),
        ("Nice-to-haves", "nice_to_haves"),
        ("Red flags", "red_flags_in_jd"),
    ]:
        items = _require_string_list(jd_block, key)
        if items:
            parts.append(f"{label}: " + ", ".join(str(x) for x in items))
    return "\n\n".join(parts)


def _build_match_item(item: object) -> MatchItem:
    if not isinstance(item, dict):
        raise ScoringParseError("invalid object item in strong_match")
    obj = cast(JsonObject, item)
    return MatchItem(
        requirement=_require_string(obj, "requirement"),
        evidence=_require_string(obj, "evidence_from_resume"),
    )


def _build_gap_item(item: object) -> GapItem:
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
