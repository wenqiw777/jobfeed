"""Strict schema checks for Stage A and Stage B LLM JSON blocks."""

from __future__ import annotations

from typing import cast

from jobfeed.domain.errors import ScoringParseError

JsonObject = dict[str, object]

_STAGE_A_KEYS = frozenset({"score", "one_line", "timing_eligible"})
_STAGE_B_KEYS = frozenset({"verdict", "jd_summary", "fit_analysis", "resume_hooks"})
_VERDICT_KEYS = frozenset({"recommendation", "confidence", "one_line"})
_JD_SUMMARY_KEYS = frozenset(
    {"role_in_3_lines", "must_haves", "nice_to_haves", "red_flags_in_jd"}
)
_FIT_KEYS = frozenset({"strong_match", "gaps", "score_0_100"})
_MATCH_KEYS = frozenset({"requirement", "evidence_from_resume"})
_GAP_KEYS = frozenset({"requirement", "severity", "mitigation"})
_HOOKS_KEYS = frozenset({"lead_with", "supporting", "avoid_mentioning"})


def _validate_stage_a_shape(data: JsonObject) -> None:
    _require_exact_keys(data, _STAGE_A_KEYS, "stage_a")


def _validate_stage_b_shape(data: JsonObject) -> None:
    _require_exact_keys(data, _STAGE_B_KEYS, "stage_b")

    verdict = _require_object(data, "verdict")
    _require_exact_keys(verdict, _VERDICT_KEYS, "verdict")
    _require_int_range(verdict, "confidence", minimum=1, maximum=5)
    _require_string(verdict, "one_line")

    jd_summary = _require_object(data, "jd_summary")
    _require_exact_keys(jd_summary, _JD_SUMMARY_KEYS, "jd_summary")

    fit = _require_object(data, "fit_analysis")
    _require_exact_keys(fit, _FIT_KEYS, "fit_analysis")
    _require_object_items(fit, "strong_match", _MATCH_KEYS)
    _require_object_items(fit, "gaps", _GAP_KEYS)

    hooks = _require_object(data, "resume_hooks")
    _require_exact_keys(hooks, _HOOKS_KEYS, "resume_hooks")
    _require_string_list(hooks, "avoid_mentioning")


def _require_exact_keys(
    data: JsonObject,
    expected: frozenset[str],
    label: str,
) -> None:
    actual = frozenset(data)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    parts: list[str] = []
    if missing:
        parts.append("missing " + ", ".join(missing))
    if extra:
        parts.append("extra " + ", ".join(extra))
    raise ScoringParseError(f"{label} keys invalid: {'; '.join(parts)}")


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


def _require_object_items(
    data: JsonObject,
    key: str,
    expected_keys: frozenset[str],
) -> None:
    for item in _require_list(data, key):
        if not isinstance(item, dict):
            raise ScoringParseError(f"invalid object item in {key}")
        _require_exact_keys(cast(JsonObject, item), expected_keys, key)


def _require_string_list(data: JsonObject, key: str) -> None:
    for item in _require_list(data, key):
        if not isinstance(item, str) or not item:
            raise ScoringParseError(f"invalid string item in {key}")


def _require_string(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ScoringParseError(f"missing or invalid string: {key}")
    return value


def _require_int_range(
    data: JsonObject,
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoringParseError(f"missing or invalid integer: {key}")
    if not minimum <= value <= maximum:
        raise ScoringParseError(f"integer out of range: {key}")
