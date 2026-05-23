"""Pure scoring helpers for prompts and LLM response normalization."""

from __future__ import annotations

import json
from typing import cast

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    MatchItem,
    Message,
    StageAResult,
    StageBResult,
    Verdict,
)
from jobfeed.domain.types import VALID_SEVERITIES, Severity

JsonObject = dict[str, object]

STAGE_A_SYSTEM_PROMPT = "You are a precise job fit scoring assistant."
STAGE_B_SYSTEM_PROMPT = "You are a precise job application review assistant."
MIN_FENCED_JSON_LINES = 2
# Stage A/B retry cap: a job that has errored this many times stops being
# retried (drops out of the pending queues) and surfaces in needs_attention.
# Single source of truth shared by both store backends.
MAX_STAGE_RETRIES = 3
STAGE_A_JSON_CONTRACT = (
    'Return only JSON: {"score": 0, "one_line": "short reason", '
    '"timing_eligible": "eligible|maybe|ineligible"}. '
    "score must be an integer from 0 to 100."
)
STAGE_B_JSON_CONTRACT = (
    "Return only JSON with block_a.verdict, block_b.summary, "
    "block_c.score_0_100, block_c.strong_match[], block_c.gaps[], "
    "and block_e.hooks[]. verdict must be apply, consider, or skip; "
    "gap severity must be critical, major, or minor."
)


def render_stage_a_prompt(
    jd_text: str,
    resume_md: str,
    rubric_md: str,
) -> list[Message]:
    """Render the Stage A scoring prompt as adapter-neutral messages.

    Args:
        jd_text: Job description text to score.
        resume_md: Resume Markdown used as fit evidence.
        rubric_md: Scoring rubric Markdown.

    Returns:
        Chat messages ready for an LLM adapter.
    """
    user_content = (
        "Score this job against the resume and rubric.\n\n"
        f"## Job Description\n{jd_text}\n\n"
        f"## Resume\n{resume_md}\n\n"
        f"## Rubric\n{rubric_md}\n\n"
        f"{STAGE_A_JSON_CONTRACT}"
    )
    return [
        Message(role="system", content=STAGE_A_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


def render_stage_b_prompt(
    jd_text: str,
    resume_md: str,
    rubric_md: str,
) -> list[Message]:
    """Render the Stage B detailed review prompt.

    Args:
        jd_text: Job description text to review.
        resume_md: Resume Markdown used as evidence.
        rubric_md: Detailed review rubric Markdown.

    Returns:
        Chat messages ready for an LLM adapter.
    """
    user_content = (
        "Review this job for application readiness.\n\n"
        f"## Job Description\n{jd_text}\n\n"
        f"## Resume\n{resume_md}\n\n"
        f"## Rubric\n{rubric_md}\n\n"
        f"{STAGE_B_JSON_CONTRACT}"
    )
    return [
        Message(role="system", content=STAGE_B_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


def parse_stage_a_response(
    raw: str,
    model: str,
    prompt_hash: str,
    resume_hash: str,
    cost_usd: float | None = None,
) -> StageAResult:
    """Parse and validate the canonical Stage A raw LLM response.

    Args:
        raw: Raw LLM response, optionally wrapped in a Markdown JSON fence.
        model: Model identifier supplied by the caller.
        prompt_hash: Prompt content hash supplied by the service layer.
        resume_hash: Resume content hash supplied by the service layer.
        cost_usd: LLM cost supplied by the adapter or caller, or None when unknown.

    Returns:
        Normalized Stage A result.

    Raises:
        ScoringParseError: If the response is not valid Stage A JSON.
    """
    try:
        parsed = _parse_json_object(raw)
        score = _require_int(parsed, "score")
        return StageAResult(
            score=score,
            one_line=_require_string(parsed, "one_line"),
            timing_eligible=_require_string(parsed, "timing_eligible"),
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
    """Parse and validate the canonical Stage B raw LLM response.

    Args:
        raw: Raw LLM response, optionally wrapped in a Markdown JSON fence.
        model: Model identifier supplied by the caller.
        prompt_hash: Prompt content hash supplied by the service layer.
        resume_hash: Resume content hash supplied by the service layer.
        cost_usd: LLM cost supplied by the adapter or caller, or None when unknown.

    Returns:
        Normalized Stage B result with raw block JSON preserved.

    Raises:
        ScoringParseError: If any required Stage B block or field is invalid.
    """
    try:
        parsed = _parse_json_object(raw)
        block_a = _require_object(parsed, "block_a")
        block_b = _require_object(parsed, "block_b")
        block_c = _require_object(parsed, "block_c")
        block_e = _require_object(parsed, "block_e")
        fit_analysis = FitAnalysis(
            score=_require_int(block_c, "score_0_100"),
            strengths=_build_match_items(_require_list(block_c, "strong_match")),
            gaps=_build_gap_items(_require_list(block_c, "gaps")),
        )
        return StageBResult(
            verdict=Verdict(_require_string(block_a, "verdict")),
            jd_summary=_require_string(block_b, "summary"),
            fit_analysis=fit_analysis,
            resume_hooks=_build_string_items(_require_list(block_e, "hooks"), "hooks"),
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


def _require_int(data: JsonObject, key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoringParseError(f"missing or invalid integer: {key}")
    return value


def _build_match_items(items: list[object]) -> list[MatchItem]:
    return [_build_match_item(item) for item in items]


def _build_match_item(item: object) -> MatchItem:
    item_object = _require_item_object(item, "strong_match")
    return MatchItem(
        requirement=_require_string(item_object, "requirement"),
        evidence=_require_string(item_object, "evidence"),
    )


def _build_gap_items(items: list[object]) -> list[GapItem]:
    return [_build_gap_item(item) for item in items]


def _build_gap_item(item: object) -> GapItem:
    item_object = _require_item_object(item, "gaps")
    return GapItem(
        requirement=_require_string(item_object, "requirement"),
        severity=_require_severity(item_object),
        mitigation=_require_string(item_object, "mitigation"),
    )


def _build_string_items(items: list[object], field_name: str) -> list[str]:
    strings: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise ScoringParseError(f"invalid string item in {field_name}")
        strings.append(item)
    return strings


def _require_item_object(item: object, field_name: str) -> JsonObject:
    if not isinstance(item, dict):
        raise ScoringParseError(f"invalid object item in {field_name}")
    return cast(JsonObject, item)


def _require_severity(item: JsonObject) -> Severity:
    severity = _require_string(item, "severity")
    if severity not in VALID_SEVERITIES:
        raise ScoringParseError("invalid gap severity")
    return cast(Severity, severity)


__all__ = [
    "MAX_STAGE_RETRIES",
    "parse_stage_a_response",
    "parse_stage_b_response",
    "render_stage_a_prompt",
    "render_stage_b_prompt",
]
