"""Deterministic boundary for seniority eligibility.

The rule layer handles only high-confidence evidence. Ambiguous titles and
scope intentionally return ``unclear`` so a separate classifier can decide
without contaminating technical-fit scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SeniorityResult = Literal["in_scope", "out_of_scope", "unclear"]
SCOPE_EXPERIENCE_YEARS = 3

_YEAR_REQUIREMENT = re.compile(
    r"\b(?P<minimum>\d{1,2})\s*(?:\+|[-\u2013]\s*\d{1,2}\+?)?\s+years?"
    r"(?:['\u2019])?\b",
    re.IGNORECASE,
)
_PREFERRED_MARKER = re.compile(
    r"\b(?:preferred|ideally|nice\s+to\s+have|bonus)\b", re.IGNORECASE
)
_REQUIRED_MARKER = re.compile(
    r"\b(?:required|requirements?|minimum|at\s+least|must\s+have)\b",
    re.IGNORECASE,
)
_EXPERIENCE_CONTEXT = re.compile(
    r"\b(?:experience|development|engineering|building|working|technical"
    r"|professional|required|requirements?|qualifications?|skills?|background"
    r"|minimum|at\s+least|must\s+have|requires?|have)\b",
    re.IGNORECASE,
)
_ENTRY_TITLE = re.compile(
    r"\b(?:intern(?:ship)?|new[\s-]?grad(?:uate)?|entry[\s-]?level|junior|jr\.?)\b",
    re.IGNORECASE,
)
_OWNERSHIP_TITLE = re.compile(r"\b(?:staff|principal|lead|manager)\b", re.IGNORECASE)
_OWNERSHIP_SCOPE = re.compile(
    r"\b(?:own\s+(?:the\s+)?architecture\s+across\s+(?:teams|the\s+organization)"
    r"|set\s+(?:the\s+)?technical\s+direction"
    r"|manage\s+(?:a\s+)?team\s+of\s+(?:engineers|developers)"
    r"|lead\s+(?:an?\s+)?engineering\s+team)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SeniorityInput:
    """Minimal posting input used by the seniority gate."""

    job_id: str
    title: str
    jd_text: str


@dataclass(frozen=True, slots=True)
class SeniorityDecision:
    """One explainable seniority-scope verdict."""

    result: SeniorityResult
    reason: str
    yoe_min: int | None
    confidence: float
    source: Literal["rule", "model"] = "rule"
    version: str = "rule-v1"


def classify_seniority_rule(title: str, jd_text: str) -> SeniorityDecision:
    """Classify only explicit seniority evidence and defer everything else.

    Args:
        title: Job title.
        jd_text: Full job-description text.

    Returns:
        High-confidence rule decision, or ``unclear`` for model review.
    """
    if _OWNERSHIP_TITLE.search(title) or _OWNERSHIP_SCOPE.search(jd_text):
        return SeniorityDecision(
            result="out_of_scope",
            reason="explicit senior ownership",
            yoe_min=_required_yoe_min(jd_text),
            confidence=1.0,
        )

    yoe_min = _required_yoe_min(jd_text)
    if yoe_min is not None:
        if yoe_min >= SCOPE_EXPERIENCE_YEARS:
            return SeniorityDecision(
                result="out_of_scope",
                reason="minimum experience is 3 years or more",
                yoe_min=yoe_min,
                confidence=1.0,
            )
        return SeniorityDecision(
            result="in_scope",
            reason="minimum experience is below 3 years",
            yoe_min=yoe_min,
            confidence=1.0,
        )

    if _ENTRY_TITLE.search(title):
        return SeniorityDecision(
            result="in_scope",
            reason="explicit entry band",
            yoe_min=None,
            confidence=1.0,
        )

    return SeniorityDecision(
        result="unclear",
        reason="no explicit seniority boundary",
        yoe_min=None,
        confidence=0.0,
    )


def _required_yoe_min(jd_text: str) -> int | None:
    requirements: list[int] = []
    for match in _YEAR_REQUIREMENT.finditer(jd_text):
        context = jd_text[max(0, match.start() - 120) : match.start()]
        surrounding = jd_text[
            max(0, match.start() - 120) : min(len(jd_text), match.end() + 160)
        ]
        if not _EXPERIENCE_CONTEXT.search(surrounding):
            continue
        preferred = list(_PREFERRED_MARKER.finditer(context))
        required = list(_REQUIRED_MARKER.finditer(context))
        last_preferred = preferred[-1].start() if preferred else -1
        last_required = required[-1].start() if required else -1
        if last_preferred > last_required:
            continue
        requirements.append(int(match.group("minimum")))
    return max(requirements) if requirements else None


__all__ = [
    "SCOPE_EXPERIENCE_YEARS",
    "SeniorityDecision",
    "SeniorityInput",
    "SeniorityResult",
    "classify_seniority_rule",
]
