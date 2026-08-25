"""Objective domain contract and deterministic scoring for unified evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

UNIFIED_EVALUATOR_VERSION = "unified-v1"

_REQUIRED_WEIGHT = 0.85
_PREFERRED_WEIGHT = 0.15
_MATCH_CREDIT = {
    "direct": 1.0,
    "adjacent": 0.60,
    "missing": 0.0,
    "unclear": 0.0,
}
_SOURCE_CREDIT: dict[str, dict[str, float]] = {
    "skill": {
        "professional": 1.0,
        "internship": 0.90,
        "project": 0.80,
        "skills": 0.65,
        "coursework": 0.45,
        "none": 0.0,
    },
    "experience": {
        "professional": 1.0,
        "internship": 0.65,
        "project": 0.25,
        "skills": 0.0,
        "coursework": 0.0,
        "none": 0.0,
    },
    "education": {
        "professional": 0.40,
        "internship": 0.30,
        "project": 0.20,
        "skills": 0.20,
        "coursework": 1.0,
        "none": 0.0,
    },
    "domain": {
        "professional": 1.0,
        "internship": 0.85,
        "project": 0.55,
        "skills": 0.25,
        "coursework": 0.35,
        "none": 0.0,
    },
}


class EligibilityStatus(StrEnum):
    """Auditable outcome for an explicit objective eligibility constraint."""

    PASS = "pass"
    FAIL = "fail"
    UNCLEAR = "unclear"


class RequirementPriority(StrEnum):
    """Whether the JD states a requirement as mandatory or preferred."""

    REQUIRED = "required"
    PREFERRED = "preferred"


class RequirementCategory(StrEnum):
    """Objective requirement categories scored against resume evidence."""

    SKILL = "skill"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    DOMAIN = "domain"


class RequirementMatch(StrEnum):
    """How directly the cited resume evidence satisfies a JD requirement."""

    DIRECT = "direct"
    ADJACENT = "adjacent"
    MISSING = "missing"
    UNCLEAR = "unclear"


class EvidenceType(StrEnum):
    """Resume section or tenure type supporting a requirement match."""

    PROFESSIONAL = "professional"
    INTERNSHIP = "internship"
    PROJECT = "project"
    SKILLS = "skills"
    COURSEWORK = "coursework"
    NONE = "none"


class MatchTier(StrEnum):
    """LLM evidence synthesis, deliberately separate from application action."""

    STRONG_MATCH = "strong_match"
    POSSIBLE_MATCH = "possible_match"
    WEAK_MATCH = "weak_match"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, kw_only=True)
class EligibilityCheck:
    """One explicit JD eligibility constraint and candidate evidence for it."""

    kind: str
    requirement: str
    status: EligibilityStatus
    candidate_evidence: str | None
    reason: str


@dataclass(frozen=True, kw_only=True)
class RequirementAssessment:
    """One required or preferred JD qualification matched to resume evidence."""

    requirement: str
    priority: RequirementPriority
    category: RequirementCategory
    match: RequirementMatch
    resume_evidence: str | None
    evidence_type: EvidenceType


@dataclass(frozen=True, kw_only=True)
class UnifiedEvaluationResult:
    """Single-pass objective evaluation with auditable evidence and provenance."""

    summary: str
    eligibility_status: EligibilityStatus
    eligibility_checks: tuple[EligibilityCheck, ...]
    requirements: tuple[RequirementAssessment, ...]
    match_score: int
    match_tier: MatchTier
    one_line: str
    ats_visibility_score: int
    model: str
    prompt_hash: str
    resume_hash: str
    evaluator_version: str
    cost_usd: float | None
    raw_result: dict[str, object]

    @property
    def result_json(self) -> dict[str, object]:
        """Return a JSON-serializable representation for persistence.

        Returns:
            Canonical evidence, score, provenance, and raw model output.
        """
        return {
            "summary": self.summary,
            "eligibility_status": self.eligibility_status.value,
            "eligibility_checks": [
                {
                    "kind": check.kind,
                    "requirement": check.requirement,
                    "status": check.status.value,
                    "candidate_evidence": check.candidate_evidence,
                    "reason": check.reason,
                }
                for check in self.eligibility_checks
            ],
            "requirements": [
                {
                    "requirement": item.requirement,
                    "priority": item.priority.value,
                    "category": item.category.value,
                    "match": item.match.value,
                    "resume_evidence": item.resume_evidence,
                    "evidence_type": item.evidence_type.value,
                }
                for item in self.requirements
            ],
            "match_score": self.match_score,
            "match_tier": self.match_tier.value,
            "one_line": self.one_line,
            "ats_visibility_score": self.ats_visibility_score,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "resume_hash": self.resume_hash,
            "evaluator_version": self.evaluator_version,
            "cost_usd": self.cost_usd,
            "raw_result": self.raw_result,
        }


def compute_match_score(requirements: Sequence[RequirementAssessment]) -> int:
    """Compute evidence-weighted qualification independently of eligibility.

    Required requirements contribute 85% and preferred requirements 15% when
    both groups exist. If the JD contains only one group, that group owns the
    full score rather than granting free points for an absent group.

    Args:
        requirements: Exhaustive required and preferred JD assessments.

    Returns:
        Deterministic integer qualification score in the inclusive 0-100 range.
    """
    required = [
        _requirement_credit(item)
        for item in requirements
        if item.priority == "required"
    ]
    preferred = [
        _requirement_credit(item)
        for item in requirements
        if item.priority == "preferred"
    ]
    if required and preferred:
        value = _REQUIRED_WEIGHT * _mean(required) + _PREFERRED_WEIGHT * _mean(
            preferred
        )
    elif required:
        value = _mean(required)
    elif preferred:
        value = _mean(preferred)
    else:
        value = 0.0
    return max(0, min(100, int(100.0 * value + 0.5)))


def _requirement_credit(item: RequirementAssessment) -> float:
    match_credit = _MATCH_CREDIT[str(item.match)]
    source_credit = _SOURCE_CREDIT[str(item.category)][str(item.evidence_type)]
    return match_credit * source_credit


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


__all__ = [
    "UNIFIED_EVALUATOR_VERSION",
    "EligibilityCheck",
    "EligibilityStatus",
    "EvidenceType",
    "MatchTier",
    "RequirementAssessment",
    "RequirementCategory",
    "RequirementMatch",
    "RequirementPriority",
    "UnifiedEvaluationResult",
    "compute_match_score",
]
