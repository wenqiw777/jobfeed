"""Unit tests for pure scoring response parsing."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import Verdict
from jobfeed.domain.scoring import (
    compute_prompt_hash,
    compute_resume_hash,
    parse_stage_a_response,
    parse_stage_b_response,
    render_stage_a_prompt,
    render_stage_b_prompt,
)

STAGE_A_SCORE = 85
STAGE_B_SCORE = 92
SHA256_HEX_LENGTH = 64
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_stage_b_payload() -> dict[str, object]:
    """Create a canonical raw Stage B payload for parser tests.

    Returns:
        Raw block object matching the legacy Stage B LLM contract.
    """
    return {
        "verdict": {
            "recommendation": "apply",
            "confidence": 4,
            "one_line": "Strong Python match for hiring workflow role.",
        },
        "jd_summary": {
            "role_in_3_lines": "Builds Python services for hiring workflows.",
            "must_haves": ["Python"],
            "nice_to_haves": [],
            "red_flags_in_jd": [],
        },
        "fit_analysis": {
            "score_0_100": STAGE_B_SCORE,
            "strong_match": [
                {
                    "requirement": "Python",
                    "evidence_from_resume": "Built Python data pipelines.",
                }
            ],
            "gaps": [
                {
                    "requirement": "Kubernetes",
                    "severity": "minor",
                    "mitigation": "Emphasize container deployment work.",
                }
            ],
        },
        "resume_hooks": {
            "lead_with": "Mention local-first job automation.",
            "supporting": [],
            "avoid_mentioning": [],
        },
    }


def test_render_stage_a_prompt_returns_messages() -> None:
    """Stage A prompt rendering should produce adapter-neutral messages."""
    messages = render_stage_a_prompt("JD", "Resume", "Rubric")

    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert "JD" in messages[1].content
    assert "score" in messages[1].content
    assert "0 to 100" in messages[1].content
    assert "timing_eligible" in messages[1].content


def test_render_stage_b_prompt_declares_canonical_json_shape() -> None:
    """Stage B prompt should name the exact JSON blocks the parser expects."""
    messages = render_stage_b_prompt("JD", "Resume", "Rubric")
    content = messages[1].content

    assert "block_a.verdict" in content
    assert "block_c.score_0_100" in content
    assert "block_c.strong_match[]" in content
    assert "block_e.hooks[]" in content
    assert "skip" in content


def test_parse_stage_a_response_returns_result() -> None:
    """Stage A JSON should parse into a StageAResult with caller metadata."""
    result = parse_stage_a_response(
        '{"score": 85, "one_line": "Good fit", "timing_eligible": "eligible"}',
        model="mock",
        prompt_hash="h1",
        resume_hash="h2",
    )

    assert result.score == STAGE_A_SCORE
    assert result.model == "mock"
    assert result.prompt_hash == "h1"
    assert result.resume_hash == "h2"
    assert result.cost_usd is None


def test_parse_stage_a_response_handles_markdown_json_fence() -> None:
    """Stage A parser should accept markdown-wrapped JSON responses."""
    raw = (
        "```json\n"
        '{"score": 85, "one_line": "Good fit", "timing_eligible": "eligible"}\n'
        "```"
    )

    result = parse_stage_a_response(
        raw,
        model="mock",
        prompt_hash="h1",
        resume_hash="h2",
    )

    assert result.score == STAGE_A_SCORE


def test_parse_stage_a_accepts_legacy_timing_vocabulary() -> None:
    """Stage A parser should accept legacy timing_eligible vocabulary."""
    raw = '{"score": 85, "one_line": "Good fit", "timing_eligible": "mismatch"}'
    result = parse_stage_a_response(
        raw, model="mock", prompt_hash="h1", resume_hash="h2"
    )
    assert result.timing_eligible == "mismatch"


@pytest.mark.parametrize(
    "raw",
    [
        '{"score": 150, "one_line": "Too high", "timing_eligible": "eligible"}',
        '{"score": 85, "one_line": ',
        "I cannot comply with that request.",
    ],
)
def test_parse_stage_a_response_rejects_invalid_payloads(raw: str) -> None:
    """Stage A parser should reject invalid, truncated, or refusal text.

    Args:
        raw: Invalid raw LLM response.
    """
    with pytest.raises(ScoringParseError) as exc_info:
        parse_stage_a_response(raw, model="mock", prompt_hash="h1", resume_hash="h2")

    assert exc_info.value.raw_response == raw


def test_parse_stage_a_response_detects_refusal_variants() -> None:
    """Stage A parser should detect various refusal patterns."""
    refusals = [
        "I'm sorry, I cannot evaluate this.",
        "I apologize but I cannot help.",
        "Sure, as an AI assistant I can help",
    ]
    for raw in refusals:
        with pytest.raises(ScoringParseError, match="LLM refusal"):
            parse_stage_a_response(
                raw, model="mock", prompt_hash="h1", resume_hash="h2"
            )


def test_parse_stage_b_response_builds_fit_analysis() -> None:
    """Stage B JSON should parse into normalized fit analysis fields."""
    raw = json.dumps(make_stage_b_payload())

    result = parse_stage_b_response(
        raw,
        model="mock",
        prompt_hash="h1",
        resume_hash="h2",
    )

    assert result.verdict == Verdict.APPLY
    assert "Builds Python services for hiring workflows." in result.jd_summary
    assert result.fit_analysis.score == STAGE_B_SCORE
    assert result.fit_analysis.strengths[0].requirement == "Python"
    assert result.fit_analysis.strengths[0].evidence == "Built Python data pipelines."
    assert result.fit_analysis.gaps[0].severity == "minor"
    assert result.resume_hooks == ["Mention local-first job automation."]
    assert result.cost_usd is None


def test_parse_stage_b_response_maps_legacy_severity() -> None:
    """Stage B parser should map blocker->critical, notable->major."""
    payload = make_stage_b_payload()
    fit = payload["fit_analysis"]
    assert isinstance(fit, dict)
    fit["gaps"] = [
        {
            "requirement": "Critical skill",
            "severity": "blocker",
            "mitigation": None,
        },
        {
            "requirement": "Important skill",
            "severity": "notable",
            "mitigation": "Frame existing work.",
        },
    ]

    result = parse_stage_b_response(
        json.dumps(payload), model="mock", prompt_hash="h1", resume_hash="h2"
    )

    assert result.fit_analysis.gaps[0].severity == "critical"
    assert result.fit_analysis.gaps[0].mitigation == ""
    assert result.fit_analysis.gaps[1].severity == "major"
    assert result.fit_analysis.gaps[1].mitigation == "Frame existing work."


def test_parse_stage_b_response_preserves_raw_blocks() -> None:
    """Stage B parser should preserve the exact parsed raw block object."""
    payload = make_stage_b_payload()
    result = parse_stage_b_response(
        json.dumps(payload),
        model="mock",
        prompt_hash="h1",
        resume_hash="h2",
    )

    assert result.raw_blocks == payload


def test_parse_stage_b_response_rejects_missing_fit_keys() -> None:
    """Stage B parser should reject missing required fit_analysis fields."""
    payload = make_stage_b_payload()
    fit = payload["fit_analysis"]
    assert isinstance(fit, dict)
    del fit["score_0_100"]

    with pytest.raises(ScoringParseError):
        parse_stage_b_response(
            json.dumps(payload),
            model="mock",
            prompt_hash="h1",
            resume_hash="h2",
        )


@pytest.mark.parametrize(
    "missing_block", ["verdict", "jd_summary", "fit_analysis", "resume_hooks"]
)
def test_parse_stage_b_response_rejects_missing_required_blocks(
    missing_block: str,
) -> None:
    """Stage B parser should reject missing top-level block objects.

    Args:
        missing_block: Top-level block to remove from the canonical payload.
    """
    payload = make_stage_b_payload()
    del payload[missing_block]
    raw = json.dumps(payload)

    with pytest.raises(ScoringParseError) as exc_info:
        parse_stage_b_response(
            raw,
            model="mock",
            prompt_hash="h1",
            resume_hash="h2",
        )

    assert exc_info.value.raw_response == raw


def test_compute_prompt_hash_is_deterministic() -> None:
    """compute_prompt_hash should return the same hash for the same input."""
    h1 = compute_prompt_hash("test prompt")
    h2 = compute_prompt_hash("test prompt")
    assert h1 == h2
    assert len(h1) == SHA256_HEX_LENGTH


def test_compute_prompt_hash_changes_with_input() -> None:
    """compute_prompt_hash should change when the prompt changes."""
    h1 = compute_prompt_hash("prompt A")
    h2 = compute_prompt_hash("prompt B")
    assert h1 != h2


def test_compute_resume_hash_is_deterministic() -> None:
    """compute_resume_hash should return the same hash for the same input."""
    h1 = compute_resume_hash("my resume")
    h2 = compute_resume_hash("my resume")
    assert h1 == h2


def test_domain_scoring_has_no_external_imports() -> None:
    """Domain scoring should only import standard library and domain modules."""
    path = PROJECT_ROOT / "src/jobfeed/domain/scoring.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(
                alias.name for alias in node.names if not _is_allowed_import(alias.name)
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            violations.extend(
                [node.module] if not _is_allowed_import(node.module) else []
            )

    assert violations == []


def _is_allowed_import(module: str) -> bool:
    return module in {"__future__", "json", "typing", "hashlib"} or module.startswith(
        "jobfeed.domain."
    )
