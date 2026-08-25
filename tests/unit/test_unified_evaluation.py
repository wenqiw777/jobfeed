"""Contract tests for the single-pass objective job evaluator."""

from __future__ import annotations

import json

import pytest

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.models import UnifiedEvaluationResult
from jobfeed.domain.unified_evaluation import (
    MatchTier,
    RequirementAssessment,
    compute_match_score,
)
from jobfeed.domain.unified_evaluation_parse import parse_unified_evaluation_response

EXPECTED_MATCH_SCORE = 75
EXPECTED_ATS_SCORE = 72
FULL_MATCH_SCORE = 100
PROJECT_DOMAIN_SCORE = 55
INTERNSHIP_EXPERIENCE_SCORE = 65


def _payload() -> dict[str, object]:
    return {
        "summary": "Backend role requiring Python and production ownership.",
        "eligibility_status": "pass",
        "eligibility_checks": [],
        "requirements": [
            {
                "requirement": "Production Python experience",
                "priority": "required",
                "category": "skill",
                "match": "direct",
                "resume_evidence": "Built and operated Python services at Acme.",
                "evidence_type": "professional",
            },
            {
                "requirement": "Two years of production ownership",
                "priority": "required",
                "category": "experience",
                "match": "direct",
                "resume_evidence": "Owned a production service during an internship.",
                "evidence_type": "internship",
            },
            {
                "requirement": "Payments domain experience",
                "priority": "preferred",
                "category": "domain",
                "match": "adjacent",
                "resume_evidence": "Built a checkout project.",
                "evidence_type": "project",
            },
        ],
        "match_tier": "strong_match",
        "one_line": (
            "Direct Python evidence; production tenure and payments depth are weaker."
        ),
        "ats_visibility_score": EXPECTED_ATS_SCORE,
    }


def _parse(payload: dict[str, object]) -> UnifiedEvaluationResult:
    return parse_unified_evaluation_response(
        json.dumps(payload),
        model="mock-model",
        prompt_hash="prompt-hash",
        resume_hash="resume-hash",
        cost_usd=0.012,
    )


def test_parser_builds_contract_and_computes_score_from_evidence() -> None:
    """The LLM supplies evidence while code owns the qualification score."""
    payload = _payload()

    result = _parse(payload)

    assert result.match_score == EXPECTED_MATCH_SCORE
    assert result.match_tier is MatchTier.STRONG_MATCH
    assert result.ats_visibility_score == EXPECTED_ATS_SCORE
    assert result.model == "mock-model"
    assert result.prompt_hash == "prompt-hash"
    assert result.resume_hash == "resume-hash"
    assert result.evaluator_version == "unified-v1"
    assert isinstance(result, UnifiedEvaluationResult)
    assert result.cost_usd == pytest.approx(0.012)
    assert result.raw_result == payload
    assert result.result_json["match_score"] == EXPECTED_MATCH_SCORE
    assert result.result_json["eligibility_checks"] == []


def test_score_does_not_mechanically_replace_llm_match_tier() -> None:
    """A low deterministic score must not turn an evidence tier into an action rule."""
    payload = _payload()
    payload["match_tier"] = "weak_match"

    result = _parse(payload)

    assert result.match_score == EXPECTED_MATCH_SCORE
    assert result.match_tier is MatchTier.WEAK_MATCH


def test_confirmed_eligibility_failure_only_forces_tier_not_score() -> None:
    """Eligibility is a separate gate and must not contaminate qualification."""
    payload = _payload()
    payload["eligibility_status"] = "fail"
    payload["eligibility_checks"] = [
        {
            "kind": "graduation_window",
            "requirement": "Must graduate by June 2026",
            "status": "fail",
            "candidate_evidence": "Education section states May 2027 graduation.",
            "reason": "The documented graduation date is outside the JD window.",
        }
    ]

    result = _parse(payload)

    assert result.match_score == EXPECTED_MATCH_SCORE
    assert result.match_tier is MatchTier.INELIGIBLE


def test_source_strength_is_stricter_for_experience_and_domain() -> None:
    """A project or skills-list mention is not equivalent to paid domain tenure."""
    professional = RequirementAssessment(
        requirement="Payments experience",
        priority="required",
        category="domain",
        match="direct",
        resume_evidence="Shipped payment processing at Acme.",
        evidence_type="professional",
    )
    project = RequirementAssessment(
        requirement="Payments experience",
        priority="required",
        category="domain",
        match="direct",
        resume_evidence="Built a checkout demo.",
        evidence_type="project",
    )
    internship = RequirementAssessment(
        requirement="Three years of professional experience",
        priority="required",
        category="experience",
        match="direct",
        resume_evidence="Completed one software internship.",
        evidence_type="internship",
    )

    assert compute_match_score([professional]) == FULL_MATCH_SCORE
    assert compute_match_score([project]) == PROJECT_DOMAIN_SCORE
    assert compute_match_score([internship]) == INTERNSHIP_EXPERIENCE_SCORE


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p.update({"recommendation": "apply"}), "keys invalid"),
        (lambda p: p.update({"company_growth": "high"}), "keys invalid"),
        (lambda p: p.update({"ats_visibility_score": 101}), "out of range"),
        (lambda p: p.update({"match_tier": "apply"}), "invalid match_tier"),
    ],
)
def test_parser_rejects_forbidden_or_invalid_output(
    mutation: object,
    message: str,
) -> None:
    """Strict shape validation excludes action and subjective scoring fields."""
    payload = _payload()
    assert callable(mutation)
    mutation(payload)

    with pytest.raises(ScoringParseError, match=message):
        _parse(payload)


def test_parser_rejects_unconfirmed_ineligible_tier() -> None:
    """Ineligible requires at least one explicit failed eligibility check."""
    payload = _payload()
    payload["match_tier"] = "ineligible"

    with pytest.raises(ScoringParseError, match="requires a failed eligibility"):
        _parse(payload)


def test_parser_rejects_inconsistent_eligibility_aggregate() -> None:
    """The declared eligibility status must agree with its auditable checks."""
    payload = _payload()
    payload["eligibility_status"] = "pass"
    payload["eligibility_checks"] = [
        {
            "kind": "graduation_window",
            "requirement": "Graduate by June 2026",
            "status": "unclear",
            "candidate_evidence": None,
            "reason": "Resume does not state a graduation month.",
        }
    ]

    with pytest.raises(ScoringParseError, match="eligibility_status inconsistent"):
        _parse(payload)


def test_parser_requires_positive_evidence_for_eligibility_failure() -> None:
    """Missing candidate information is unclear and cannot prove ineligibility."""
    payload = _payload()
    payload["eligibility_status"] = "fail"
    payload["eligibility_checks"] = [
        {
            "kind": "clearance",
            "requirement": "Active TS/SCI clearance required",
            "status": "fail",
            "candidate_evidence": None,
            "reason": "The resume does not mention clearance.",
        }
    ]

    with pytest.raises(ScoringParseError, match="failed eligibility requires evidence"):
        _parse(payload)


def test_parser_requires_match_and_evidence_consistency() -> None:
    """Direct or adjacent matches must cite evidence rather than a bare claim."""
    payload = _payload()
    requirements = payload["requirements"]
    assert isinstance(requirements, list)
    requirement = requirements[0]
    assert isinstance(requirement, dict)
    requirement["resume_evidence"] = None
    requirement["evidence_type"] = "none"

    with pytest.raises(
        ScoringParseError,
        match="matched requirement requires evidence",
    ):
        _parse(payload)
