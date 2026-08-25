"""Strict parser for the single-pass objective evaluation response."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypeVar, cast

from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.scoring_json import JsonObject, _require_list, _require_string
from jobfeed.domain.scoring_parse import _parse_or_refusal, _require_int
from jobfeed.domain.scoring_refusal import _detect_refusal_fields
from jobfeed.domain.scoring_schema import _require_exact_keys, _require_int_range
from jobfeed.domain.unified_evaluation import (
    UNIFIED_EVALUATOR_VERSION,
    EligibilityCheck,
    EligibilityStatus,
    EvidenceType,
    MatchTier,
    RequirementAssessment,
    RequirementCategory,
    RequirementMatch,
    RequirementPriority,
    UnifiedEvaluationResult,
    compute_match_score,
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "summary",
        "eligibility_status",
        "eligibility_checks",
        "requirements",
        "match_tier",
        "one_line",
        "ats_visibility_score",
    }
)
_ELIGIBILITY_KEYS = frozenset(
    {"kind", "requirement", "status", "candidate_evidence", "reason"}
)
_REQUIREMENT_KEYS = frozenset(
    {
        "requirement",
        "priority",
        "category",
        "match",
        "resume_evidence",
        "evidence_type",
    }
)
_EnumT = TypeVar("_EnumT", bound=StrEnum)


def parse_unified_evaluation_response(  # noqa: PLR0913 - provenance is explicit
    raw: str,
    model: str,
    prompt_hash: str,
    resume_hash: str,
    cost_usd: float | None = None,
    evaluator_version: str = UNIFIED_EVALUATOR_VERSION,
    resume_text: str | None = None,
    job_text: str | None = None,
) -> UnifiedEvaluationResult:
    """Parse one exact-schema response and compute its qualification score.

    Args:
        raw: Raw LLM response, optionally wrapped in a Markdown JSON fence.
        model: Model identifier supplied by the caller.
        prompt_hash: Hash of the rendered objective system prompt.
        resume_hash: Hash of the resume supplied to the model.
        cost_usd: Optional adapter-reported request cost.
        evaluator_version: Deterministic aggregation contract version.

    Returns:
        Validated objective evaluation with code-computed match score.

    Raises:
        ScoringParseError: If shape, enums, evidence, or aggregates are invalid.
    """
    try:
        parsed = _parse_or_refusal(raw, detect_truncation=True)
        _require_exact_keys(parsed, _TOP_LEVEL_KEYS, "unified_evaluation")
        _detect_refusal_fields(parsed)
        checks = _parse_checks(parsed)
        requirements = _parse_requirements(parsed)
        _validate_source_excerpts(
            checks,
            requirements,
            resume_text=resume_text,
            job_text=job_text,
        )
        eligibility = _enum_value(
            EligibilityStatus,
            _require_string(parsed, "eligibility_status"),
            "eligibility_status",
        )
        aggregate = _aggregate_eligibility(checks)
        if eligibility is not aggregate:
            raise ScoringParseError("eligibility_status inconsistent with checks")
        tier = _enum_value(
            MatchTier,
            _require_string(parsed, "match_tier"),
            "match_tier",
        )
        if eligibility is EligibilityStatus.FAIL:
            tier = MatchTier.INELIGIBLE
        elif tier is MatchTier.INELIGIBLE:
            raise ScoringParseError(
                "ineligible match_tier requires a failed eligibility check"
            )
        _require_int_range(parsed, "ats_visibility_score", minimum=0, maximum=100)
        return UnifiedEvaluationResult(
            summary=_require_string(parsed, "summary"),
            eligibility_status=eligibility,
            eligibility_checks=checks,
            requirements=requirements,
            match_score=compute_match_score(requirements),
            match_tier=tier,
            one_line=_require_string(parsed, "one_line"),
            ats_visibility_score=_require_int(parsed, "ats_visibility_score"),
            model=model,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
            evaluator_version=evaluator_version,
            cost_usd=cost_usd,
            raw_result=parsed,
        )
    except ScoringParseError as exc:
        exc.raw_response = exc.raw_response or raw
        raise
    except ValueError as exc:
        raise ScoringParseError(str(exc), raw_response=raw) from exc


def _parse_checks(data: JsonObject) -> tuple[EligibilityCheck, ...]:
    checks: list[EligibilityCheck] = []
    for item in _require_list(data, "eligibility_checks"):
        obj = _object_item(item, "eligibility_checks")
        _require_exact_keys(obj, _ELIGIBILITY_KEYS, "eligibility_check")
        status = _enum_value(
            EligibilityStatus,
            _require_string(obj, "status"),
            "eligibility status",
        )
        evidence = _nullable_string(obj, "candidate_evidence")
        if status is EligibilityStatus.FAIL and evidence is None:
            raise ScoringParseError("failed eligibility requires evidence")
        checks.append(
            EligibilityCheck(
                kind=_require_string(obj, "kind"),
                requirement=_require_string(obj, "requirement"),
                status=status,
                candidate_evidence=evidence,
                reason=_require_string(obj, "reason"),
            )
        )
    return tuple(checks)


def _parse_requirements(data: JsonObject) -> tuple[RequirementAssessment, ...]:
    assessments: list[RequirementAssessment] = []
    seen: set[str] = set()
    for item in _require_list(data, "requirements"):
        obj = _object_item(item, "requirements")
        _require_exact_keys(obj, _REQUIREMENT_KEYS, "requirement")
        requirement = _require_string(obj, "requirement")
        normalized = " ".join(requirement.casefold().split())
        if normalized in seen:
            raise ScoringParseError("duplicate requirement")
        seen.add(normalized)
        match = _enum_value(
            RequirementMatch,
            _require_string(obj, "match"),
            "requirement match",
        )
        evidence = _nullable_string(obj, "resume_evidence")
        evidence_type = _enum_value(
            EvidenceType,
            _require_string(obj, "evidence_type"),
            "evidence_type",
        )
        if match in {RequirementMatch.MISSING, RequirementMatch.UNCLEAR}:
            evidence = None
            evidence_type = EvidenceType.NONE
        _validate_requirement_evidence(match, evidence, evidence_type)
        assessments.append(
            RequirementAssessment(
                requirement=requirement,
                priority=_enum_value(
                    RequirementPriority,
                    _require_string(obj, "priority"),
                    "requirement priority",
                ),
                category=_enum_value(
                    RequirementCategory,
                    _require_string(obj, "category"),
                    "requirement category",
                ),
                match=match,
                resume_evidence=evidence,
                evidence_type=evidence_type,
            )
        )
    return tuple(assessments)


def _aggregate_eligibility(
    checks: tuple[EligibilityCheck, ...],
) -> EligibilityStatus:
    statuses = {check.status for check in checks}
    if EligibilityStatus.FAIL in statuses:
        return EligibilityStatus.FAIL
    if EligibilityStatus.UNCLEAR in statuses:
        return EligibilityStatus.UNCLEAR
    return EligibilityStatus.PASS


def _validate_source_excerpts(
    checks: tuple[EligibilityCheck, ...],
    requirements: tuple[RequirementAssessment, ...],
    *,
    resume_text: str | None,
    job_text: str | None,
) -> None:
    """Require model citations to be token-normalized source excerpts."""
    if job_text is not None:
        for check in checks:
            _require_excerpt(
                job_text, check.requirement, "requirement not found in job"
            )
        for item in requirements:
            _require_excerpt(job_text, item.requirement, "requirement not found in job")
    if resume_text is not None:
        for check in checks:
            if check.candidate_evidence is not None:
                _require_excerpt(
                    resume_text,
                    check.candidate_evidence,
                    "evidence not found in resume",
                )
        for item in requirements:
            if item.resume_evidence is not None:
                _require_excerpt(
                    resume_text,
                    item.resume_evidence,
                    "evidence not found in resume",
                )


def _require_excerpt(source: str, excerpt: str, message: str) -> None:
    source_tokens = " ".join(re.findall(r"\w+", source.casefold()))
    excerpt_tokens = " ".join(re.findall(r"\w+", excerpt.casefold()))
    if not excerpt_tokens or excerpt_tokens not in source_tokens:
        raise ScoringParseError(message)


def _validate_requirement_evidence(
    match: RequirementMatch,
    evidence: str | None,
    evidence_type: EvidenceType,
) -> None:
    matched = match in {RequirementMatch.DIRECT, RequirementMatch.ADJACENT}
    if matched and (evidence is None or evidence_type is EvidenceType.NONE):
        raise ScoringParseError("matched requirement requires evidence")


def _object_item(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ScoringParseError(f"invalid object item in {label}")
    return cast(JsonObject, value)


def _nullable_string(data: JsonObject, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ScoringParseError(f"invalid nullable string: {key}")
    return value


def _enum_value(
    enum_type: type[_EnumT],
    value: str,
    label: str,
) -> _EnumT:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ScoringParseError(f"invalid {label}") from exc


__all__ = ["parse_unified_evaluation_response"]
