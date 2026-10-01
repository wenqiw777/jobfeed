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
_NON_INTERNSHIP_REASON = "minimum non-internship experience is at least 3 years"

_YEAR_REQUIREMENT = re.compile(
    r"(?<!\d)(?P<minimum>\d{1,2})\s*(?:\+|[-\u2013]\s*\d{1,2}\+?)?\s+years?"
    r"(?:\s+to\s+\d{1,2}\s+years?)?"
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
_YOE_TAIL = re.compile(
    r"^\s*(?:of\s+)?(?:[\w/-]+\s+){0,5}"
    r"(?:experience|exp\.?|development|engineering|building|working|developing)\b",
    re.IGNORECASE,
)
_DEGREE_TAIL = re.compile(
    r"^\s+with\s+(?:BS\b|BA\b|Bachelor|Master|Ph\.?D\.?)", re.IGNORECASE
)
_COMPANY_HISTORY = re.compile(
    r"\b(?:company|we|our|team|customers|clients|has|have|served|founded|"
    r"provided|led\s+by|been\s+around|world\s+of)\b|"
    r"\b(?:with|for)\s+(?:over|more\s+than)\s*$",
    re.IGNORECASE,
)
_DIRECT_REQUIREMENT = re.compile(
    r"\b(?:required|requires?|minimum|at\s+least|must\s+have)\b",
    re.IGNORECASE,
)
_ALTERNATIVE_GAP = re.compile(r"[;,]\s*or|\bor\s*$", re.IGNORECASE)
_MAX_ALTERNATIVE_GAP = 180
_MULTILEVEL_TITLE = re.compile(
    r"\bassociate\s+or\s+experienced\b|\bmultiple\s+levels\b|"
    r"\bassociate\s+software\s+engineer\s*/\s*software\s+engineer\b|"
    r"\bopen\s+rank\b|\b(?:engineer|developer)\s+I\s*[-\u2013/]\s*III\b",
    re.IGNORECASE,
)
_MULTILEVEL_BANDS = re.compile(
    r"\bE1\s*:.{0,100}\b[0-3]\s*[-\u2013]\s*[0-3](?:\.\d+)?\s+years?\s+of\s+exp"
    r".{0,180}\b(?:E2|Sr\.?\s+Engineer)\s*:",
    re.IGNORECASE | re.DOTALL,
)
_MTS_TITLE = re.compile(r"\bmember\s+of\s+technical\s+staff\b|\bMTS\b", re.IGNORECASE)
_ENTRY_TITLE = re.compile(
    r"\b(?:intern(?:ship)?|co[\s-]?op|new[\s-]?grads?(?:uates?)?|entry[\s-]?level|"
    r"junior|jr\.?|direct\s+college\s+hire)\b|"
    r"\b(?:engineer|developer)\s+(?:I|1)\b",
    re.IGNORECASE,
)
_OWNERSHIP_TITLE = re.compile(r"\b(?:staff|principal|lead|manager)\b", re.IGNORECASE)
_EXPLICIT_SENIORITY_TITLE = re.compile(
    r"\b(?:senior|sr\.?|mid[\s-]?level|midlevel)\b"
    r"|\b(?:engineer|developer|programmer)\s+(?:II|III|IV|2|3|4)\b",
    re.IGNORECASE,
)
_OWNERSHIP_SCOPE = re.compile(
    r"\b(?:own\s+(?:the\s+)?architecture\s+across\s+(?:teams|the\s+organization)"
    r"|set\s+(?:the\s+)?technical\s+direction"
    r"|manage\s+(?:a\s+)?team\s+of\s+(?:engineers|developers)"
    r"|lead\s+(?:an?\s+)?engineering\s+team)\b",
    re.IGNORECASE,
)
_JUNIOR_JD_PATH = re.compile(
    r"\b(?:looking\s+for|hiring|seeking|open\s+to|consider(?:ed|ing)?)"
    r".{0,55}\bnew[\s-]?grads?(?:uates?)?\b|"
    r"\bnew[\s-]?grads?(?:uates?)?\b.{0,55}\b(?:considered|welcome)\b|"
    r"\bentry[\s-]?level\s+experience\b|"
    r"\b0\s*[-\u2013]\s*3\s+years?\s+of\s+(?:\w+\s+){0,3}experience\b",
    re.IGNORECASE,
)
_NON_INTERNSHIP_TAIL = re.compile(
    r"^\s*(?:of\s+)?non[\s\-\u2011\u2013]+internship\s+"
    r"(?:(?:[\w/-]+\s+){0,5}|"
    r"design\s+or\s+architecture\s+(?:\([^)]{0,100}\)\s+)?"
    r"of\s+new\s+and\s+existing\s+systems\s+)experience\b",
    re.IGNORECASE,
)
_QUALIFICATION_SECTION = re.compile(
    r"\b(basic|required|minimum|preferred|desired)\s+"
    r"(?:qualifications?|requirements?)\b",
    re.IGNORECASE,
)
_NON_INTERNSHIP_ALTERNATIVE = re.compile(
    r"\bor\s+(?:an?\s+)?(?:bachelor'?s?|master'?s?|ph\.?d\.?|degree|diploma)\b|"
    r"\b(?:any|equivalent)\s+combination\b",
    re.IGNORECASE,
)
_NON_REQUIRED_EXPERIENCE = re.compile(
    r"\b(?:not\s+required|(?:do|does)\s+not\s+require|no\s+requirement)\b",
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
    entry_title = _ENTRY_TITLE.search(title)
    mts_title = _MTS_TITLE.search(title)
    ownership_title = _OWNERSHIP_TITLE.search(title) and not mts_title
    explicit_seniority_title = _EXPLICIT_SENIORITY_TITLE.search(title)
    if entry_title and (ownership_title or explicit_seniority_title):
        return SeniorityDecision(
            result="in_scope",
            reason="explicit entry band",
            yoe_min=None,
            confidence=1.0,
        )

    if _MULTILEVEL_TITLE.search(title) or _MULTILEVEL_BANDS.search(jd_text):
        return SeniorityDecision(
            result="in_scope",
            reason="multiple-level title",
            yoe_min=None,
            confidence=1.0,
        )

    if entry_title:
        return _entry_title_decision(jd_text)

    required_years = _required_yoe_min(jd_text)
    protected = _junior_or_mts_decision(
        jd_text,
        required_years=required_years,
        mts_title=bool(mts_title),
        senior_title=bool(explicit_seniority_title),
    )
    if protected is not None:
        return protected

    if ownership_title or _OWNERSHIP_SCOPE.search(jd_text):
        return SeniorityDecision(
            result="out_of_scope",
            reason="explicit senior ownership",
            yoe_min=_required_yoe_min(jd_text),
            confidence=1.0,
        )

    if explicit_seniority_title:
        return SeniorityDecision(
            result="out_of_scope",
            reason="explicit seniority title",
            yoe_min=_required_yoe_min(jd_text),
            confidence=1.0,
        )

    yoe_min = required_years
    if yoe_min is not None:
        if yoe_min > SCOPE_EXPERIENCE_YEARS:
            return SeniorityDecision(
                result="out_of_scope",
                reason="minimum experience is more than 3 years",
                yoe_min=yoe_min,
                confidence=1.0,
            )
        return SeniorityDecision(
            result="in_scope",
            reason="minimum experience is 3 years or less",
            yoe_min=yoe_min,
            confidence=1.0,
        )

    if _has_preferred_experience(jd_text):
        return SeniorityDecision(
            result="in_scope",
            reason="experience is preferred only",
            yoe_min=None,
            confidence=1.0,
        )

    return SeniorityDecision(
        result="unclear",
        reason="no explicit seniority boundary",
        yoe_min=None,
        confidence=0.0,
    )


def _entry_title_decision(jd_text: str) -> SeniorityDecision:
    required_years = _required_yoe_min(jd_text)
    if (
        required_years is not None
        and required_years > SCOPE_EXPERIENCE_YEARS
        and _has_explicit_required_years(jd_text)
    ):
        return SeniorityDecision(
            result="out_of_scope",
            reason="minimum experience is more than 3 years",
            yoe_min=required_years,
            confidence=1.0,
        )
    return SeniorityDecision(
        result="in_scope",
        reason="explicit entry band",
        yoe_min=None,
        confidence=1.0,
    )


def non_internship_experience_reason(title: str, jd_text: str) -> str | None:
    """Return only the explicit non-internship block, preserving other policies.

    Args:
        title: Job title, including any junior or mixed-level band.
        jd_text: Posting requirements and their alternatives.

    Returns:
        A reason for an explicit mismatch, otherwise None.
    """
    if "internship" not in jd_text.casefold():
        return None
    decision = classify_seniority_rule(title, jd_text)
    return decision.reason if decision.reason == _NON_INTERNSHIP_REASON else None


def _junior_or_mts_decision(
    jd_text: str,
    *,
    required_years: int | None,
    mts_title: bool,
    senior_title: bool,
) -> SeniorityDecision | None:
    """Keep explicit junior paths and neutral MTS titles out of model ambiguity."""
    non_internship_years = _required_non_internship_min(jd_text)
    if non_internship_years is not None and not _JUNIOR_JD_PATH.search(jd_text):
        return SeniorityDecision(
            result="out_of_scope",
            reason=_NON_INTERNSHIP_REASON,
            yoe_min=non_internship_years,
            confidence=1.0,
        )
    junior_years = (
        required_years is not None and required_years <= SCOPE_EXPERIENCE_YEARS
    )
    if _JUNIOR_JD_PATH.search(jd_text) or junior_years:
        return SeniorityDecision(
            result="in_scope",
            reason="explicit junior path",
            yoe_min=required_years if junior_years else None,
            confidence=1.0,
        )
    if not mts_title or senior_title or _OWNERSHIP_SCOPE.search(jd_text):
        return None
    senior_years = (
        required_years is not None and required_years > SCOPE_EXPERIENCE_YEARS
    )
    return SeniorityDecision(
        result="out_of_scope" if senior_years else "in_scope",
        reason=(
            "minimum experience is more than 3 years"
            if senior_years
            else "neutral MTS title"
        ),
        yoe_min=required_years,
        confidence=1.0,
    )


def _required_yoe_min(jd_text: str) -> int | None:
    requirements: list[tuple[int, int, int]] = []
    for match in _YEAR_REQUIREMENT.finditer(jd_text):
        if _is_required_experience(jd_text, match):
            requirements.append(
                (int(match.group("minimum")), match.start(), match.end())
            )
    if not requirements:
        return None
    groups: list[list[tuple[int, int, int]]] = [[requirements[0]]]
    for candidate in requirements[1:]:
        previous = groups[-1][-1]
        between = jd_text[previous[2] : candidate[1]]
        if (
            len(between) <= _MAX_ALTERNATIVE_GAP
            and "." not in between
            and _ALTERNATIVE_GAP.search(between)
        ):
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    return max(min(item[0] for item in group) for group in groups)


def _required_non_internship_min(jd_text: str) -> int | None:
    """Block explicit non-internship minimums without overriding viable paths."""
    matches = list(_YEAR_REQUIREMENT.finditer(jd_text))
    for match in matches:
        minimum = int(match.group("minimum"))
        if minimum < SCOPE_EXPERIENCE_YEARS:
            continue
        if match.start() and jd_text[match.start() - 1] == ".":
            continue
        if not _NON_INTERNSHIP_TAIL.match(jd_text[match.end() :]):
            continue
        sections = list(_QUALIFICATION_SECTION.finditer(jd_text[: match.start()]))
        if sections and sections[-1].group(1).casefold() in {"preferred", "desired"}:
            continue
        if not _is_required_experience(
            jd_text, match, experience_tail=_NON_INTERNSHIP_TAIL, tail_limit=240
        ):
            continue
        before = jd_text[max(0, match.start() - 180) : match.start()]
        vicinity = before + jd_text[match.start() : match.end() + 240]
        if _NON_REQUIRED_EXPERIENCE.search(vicinity):
            continue
        if _NON_INTERNSHIP_ALTERNATIVE.search(vicinity) or re.search(
            r"\b(?:degree|diploma)\s*(?:[,;]\s*)?or\s*$", before, re.IGNORECASE
        ):
            continue
        # Adjacent OR experience routes must not become independent requirements.
        if any(
            re.search(r"\bor\s*$", jd_text[left.end() : right.start()], re.IGNORECASE)
            # Domain import policy excludes itertools.
            for left, right in zip(matches, matches[1:], strict=False)  # noqa: RUF007
            if (left is match or right is match)
            and right.start() - left.end() <= _MAX_ALTERNATIVE_GAP
        ):
            continue
        return minimum
    return None


def _is_required_experience(
    jd_text: str,
    match: re.Match[str],
    *,
    experience_tail: re.Pattern[str] | None = None,
    tail_limit: int = 90,
) -> bool:
    context = jd_text[max(0, match.start() - 120) : match.start()]
    if re.search(r"\bup\s+to\s*$", context, re.IGNORECASE):
        return False
    tail = jd_text[match.end() : min(len(jd_text), match.end() + tail_limit)]
    if not ((experience_tail or _YOE_TAIL).match(tail) or _DEGREE_TAIL.match(tail)):
        return False
    sentence_start = (
        max(
            jd_text.rfind(".", 0, match.start()),
            jd_text.rfind("\n", 0, match.start()),
        )
        + 1
    )
    sentence_context = jd_text[sentence_start : match.start()]
    if _COMPANY_HISTORY.search(sentence_context) and not _DIRECT_REQUIREMENT.search(
        sentence_context
    ):
        return False
    preferred = list(_PREFERRED_MARKER.finditer(context))
    required = list(_REQUIRED_MARKER.finditer(context))
    if preferred and preferred[-1].start() > (required[-1].start() if required else -1):
        return False
    trailing_clause = re.split(r"[.;\n]", tail, maxsplit=1)[0]
    if _PREFERRED_MARKER.search(trailing_clause):
        return False
    return not re.search(r"\bin\s+lieu\s+of\b", trailing_clause, re.IGNORECASE)


def _has_preferred_experience(jd_text: str) -> bool:
    for match in _YEAR_REQUIREMENT.finditer(jd_text):
        tail = jd_text[match.end() : min(len(jd_text), match.end() + 90)]
        if _YOE_TAIL.match(tail) and _PREFERRED_MARKER.search(
            re.split(r"[.;\n]", tail, maxsplit=1)[0]
        ):
            return True
    return False


def _has_explicit_required_years(jd_text: str) -> bool:
    for match in _YEAR_REQUIREMENT.finditer(jd_text):
        if int(match.group("minimum")) <= SCOPE_EXPERIENCE_YEARS:
            continue
        if not _is_required_experience(jd_text, match):
            continue
        context = jd_text[max(0, match.start() - 80) : match.start()]
        if _DIRECT_REQUIREMENT.search(context):
            return True
    return False


__all__ = [
    "SCOPE_EXPERIENCE_YEARS",
    "SeniorityDecision",
    "SeniorityInput",
    "SeniorityResult",
    "classify_seniority_rule",
    "non_internship_experience_reason",
]
