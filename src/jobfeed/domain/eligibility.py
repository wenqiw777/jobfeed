"""Conservative hard-blocker decisions for the confirmed search policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from jobfeed.domain.ai_data_work import ai_data_work_reason
from jobfeed.domain.ml_features import (
    classify_clearance_status,
    classify_role_type,
    classify_swe_role,
)
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.seniority import classify_seniority_rule

EligibilityStatus = Literal["pending", "apply", "blocked"]
EnrollmentEligibility = Literal["eligible", "uncertain", "not_applicable"]

_US_NAME_MARKER = re.compile(
    r"\b(?:united states|u\.s\.?|usa|remote[- ]us|us[- ]remote)\b",
    re.IGNORECASE,
)
_US_STATE_CODE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|"
    r"UT|VT|VA|WA|WV|WI|WY|DC)\b"
)
_NON_US_MARKER = re.compile(
    r"\b(?:canada|mexico|singapore|india|australia|new zealand|united kingdom|"
    r"uk|ireland|germany|france|spain|italy|netherlands|sweden|switzerland|"
    r"poland|romania|portugal|brazil|argentina|japan|china|hong kong|taiwan|"
    r"south korea|israel|united arab emirates|uae|european union|eu only)\b",
    re.IGNORECASE,
)
_UNKNOWN_LOCATION = re.compile(
    r"^\s*(?:unknown|remote|multiple locations|various|n/?a)?\s*$", re.IGNORECASE
)
_ACTIVE_CLEARANCE = re.compile(
    r"\b(?:active|current|existing|already (?:have|hold|possess)|"
    r"must (?:have|hold|possess))"
    r".{0,35}\b(?:clearance|secret|ts/sci|polygraph)\b",
    re.IGNORECASE,
)
_GRADUATION_CONTEXT = re.compile(
    r"(?:graduat(?:e|es|ing|ion)|class of).{0,80}?\b(20\d{2})\b|"
    r"\b(20\d{2})\b.{0,80}?(?:graduat(?:e|es|ing|ion)|class)",
    re.IGNORECASE,
)
_RETURN_TO_SCHOOL = re.compile(
    r"\b(?:return(?:ing)? to (?:school|college|university)|continue (?:as )?a student|"
    r"remain enrolled|currently enrolled)\b",
    re.IGNORECASE,
)
_ADVANCED_DEGREE_REQUIRED = re.compile(
    r"(?:\b(?:master(?:['\u2019]s|s)?|ph\.?d\.?|doctoral)\b.{0,35}"
    r"\b(?:required|must)\b|\b(?:required|must have)\b.{0,35}"
    r"\b(?:master(?:['\u2019]s|s)?|ph\.?d\.?|doctoral)\b)",
    re.IGNORECASE,
)
_ADVANCED_DEGREE_MENTION = re.compile(
    r"\b(?:master(?:['\u2019]s|s)?|ph\.?d\.?|doctoral)\b", re.IGNORECASE
)
_BACHELOR_OR_ADVANCED = re.compile(
    r"\bbachelor(?:['\u2019]s|s)?.{0,30}\bor\b.{0,30}"
    r"\b(?:master(?:['\u2019]s|s)?|ph\.?d\.?)\b",
    re.IGNORECASE,
)
_ADVANCED_OR_EQUIVALENT = re.compile(
    r"\b(?:master(?:['\u2019]s|s)?|ph\.?d\.?|doctoral)\b.{0,55}"
    r"\b(?:or\s+)?equivalent\b",
    re.IGNORECASE,
)
_RESEARCH_SCIENTIST_TITLE = re.compile(
    r"\b(?:(?:applied\s+)?research\s+scientist|applied\s+scientist)\b",
    re.IGNORECASE,
)
_ADVANCED_DEGREE_TITLE = re.compile(
    r"\b(?:ph\.?d\.?|doctoral|doctorate)\b", re.IGNORECASE
)
_ADVANCED_COHORT_TITLE = re.compile(
    r"\b(?:ph\.?d\.?|doctoral|master(?:['\u2019]s|s))\b", re.IGNORECASE
)
_BACHELOR_PATH = re.compile(
    r"\bbachelor(?:['\u2019]s|s)?(?:\s+degree)?\b", re.IGNORECASE
)
_RESEARCH_CREDENTIAL = re.compile(
    r"\b(?:strong\s+)?(?:publication|publishing|research)\s+(?:record|track\s+record)\b"
    r"|\bpublished\s+(?:work\s+)?(?:at|in)\b"
    r"|\bpublications?\s+(?:at|in)\s+(?:top|leading|premier)[ -]tier\b",
    re.IGNORECASE,
)
_PREFERRED_MARKER = re.compile(
    r"\b(?:preferred|ideally|nice\s+to\s+have|bonus|a\s+plus|an\s+advantage)\b",
    re.IGNORECASE,
)
_PREFERRED_SECTION_MARKER = re.compile(
    r"\bpreferred\s+(?:qualifications?|experience)\b|\bpreferred\s*:"
    r"|\bways?\s+to\s+stand\s+out\b",
    re.IGNORECASE,
)
_REQUIRED_QUALIFICATION_MARKER = re.compile(
    r"\b(?:required|minimum|basic)\s+qualifications?\b"
    r"|\bqualifications?\s+required\b"
    r"|(?<!preferred\s)\b(?:requirements?|qualifications?)\b\s*:?"
    r"|\bwhat\s+we\s+need\s+to\s+see\b"
    r"|\b(?:what|who)\s+we(?:'re|\s+are)\s+looking\s+for\b"
    r"|\byou\s+have\b",
    re.IGNORECASE,
)
_DEGREE_ASSERTION_MARKER = re.compile(
    r"\b(?:you\s+(?:hold|have)|currently\s+enrolled|employer\s+will\s+accept)\b",
    re.IGNORECASE,
)
_SOFTWARE_TITLE = re.compile(
    r"\b(?:software|developer|programmer|data engineer|machine learning|ml engineer|"
    r"ai(?:\s+&\s+analytics)?|solutions? engineer|forward deployed|site reliability|"
    r"devops|cloud engineer|security engineer|embedded|firmware|founding engineer|"
    r"full[ -]?stack|back[ -]?end|front[ -]?end|platform engineer|"
    r"infrastructure engineer|product engineer|controller integration|"
    r"data analytics)\b",
    re.IGNORECASE,
)
_EXPLICIT_NON_SOFTWARE_TITLE = re.compile(
    r"\b(?:manufacturing|production|process|civil|highway|structural|geotechnical|"
    r"mechanical|chemical|industrial|thermal performance|hardware|electrical|"
    r"circuits?|asic|silicon|fpga|rtl|dram|nand|semiconductor|physical design|"
    r"verification engineer|"
    r"reliability engineer|engineer-in-training|land/site development|"
    r"instrumentation\s*&?\s*controls?|nuclear|transmission|substation|distribution|"
    r"certification engineer|license renewal|safety analysis)\b",
    re.IGNORECASE,
)
_FOREIGN_MARKET_TITLE = re.compile(r"\((?:m/w/d|w/m/d|f/m/d|d/f/m)\)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    """One user-facing eligibility state with supporting evidence."""

    status: EligibilityStatus
    reason: str | None = None
    evidence: str | None = None
    enrollment_eligibility: EnrollmentEligibility = "not_applicable"


def evaluate_eligibility(job: JobPosting) -> EligibilityResult:  # noqa: C901
    """Apply only confirmed hard blockers; preserve uncertainty as visible state.

    Args:
        job: Posting with official-JD provenance when available.

    Returns:
        Pending, Apply, or Blocked with supporting evidence.
    """
    jd = job.jd_text or ""
    normalized_jd = " ".join(jd.split())
    if contributor_work := ai_data_work_reason(job.title, job.company):
        return EligibilityResult(
            status="blocked", reason=contributor_work, evidence=job.title
        )
    if foreign_market := _FOREIGN_MARKET_TITLE.search(job.title):
        return EligibilityResult(
            status="blocked",
            reason="job title identifies a non-US hiring market",
            evidence=foreign_market.group(0),
        )
    if _EXPLICIT_NON_SOFTWARE_TITLE.search(job.title) and not _SOFTWARE_TITLE.search(
        job.title
    ):
        return EligibilityResult(
            status="blocked",
            reason="work is outside the software-related target scope",
            evidence=job.title,
        )
    if job.jd_quality != QualityBand.FULL:
        return EligibilityResult(
            status="pending", reason="official JD has not been confirmed"
        )

    location = f"{job.location}\n{jd}"
    if not (_US_NAME_MARKER.search(location) or _US_STATE_CODE.search(job.location)):
        foreign = _NON_US_MARKER.search(job.location)
        if foreign:
            return EligibilityResult(
                status="blocked",
                reason="job is outside the United States",
                evidence=foreign.group(0),
            )
        return EligibilityResult(
            status="pending",
            reason="United States location is not confirmed",
            evidence=job.location or None,
        )

    is_swe_role = (
        job.is_swe_role
        if job.is_swe_role is not None
        else classify_swe_role(job.title, jd)
    )
    if not is_swe_role:
        return EligibilityResult(
            status="blocked",
            reason="work is outside the software-related target scope",
            evidence=job.title,
        )

    seniority = classify_seniority_rule(job.title, jd)
    if seniority.result == "out_of_scope":
        return EligibilityResult(
            status="blocked", reason=seniority.reason, evidence=job.title
        )

    if classify_clearance_status(jd) == "active_required":
        match = _ACTIVE_CLEARANCE.search(jd)
        return EligibilityResult(
            status="blocked",
            reason="active clearance is required",
            evidence=match.group(0) if match else "active clearance required",
        )

    research_title = _RESEARCH_SCIENTIST_TITLE.search(job.title)
    research_credential = _required_research_credential(normalized_jd)
    title_degree = _ADVANCED_DEGREE_TITLE.search(job.title)
    if research_title and (title_degree is not None or research_credential is not None):
        evidence = (
            research_credential.group(0)
            if research_credential is not None
            else title_degree.group(0)
            if title_degree is not None
            else job.title
        )
        return EligibilityResult(
            status="blocked",
            reason="advanced research credentials are required",
            evidence=evidence,
        )

    advanced_title = _ADVANCED_COHORT_TITLE.search(job.title)
    if advanced_title and not _BACHELOR_PATH.search(job.title):
        return EligibilityResult(
            status="blocked",
            reason="an unconfirmed advanced degree is required",
            evidence=advanced_title.group(0),
        )

    advanced_requirement = _required_advanced_degree(normalized_jd)
    if advanced_requirement and not _BACHELOR_OR_ADVANCED.search(normalized_jd):
        if _ADVANCED_OR_EQUIVALENT.search(normalized_jd):
            return EligibilityResult(
                status="pending",
                reason="advanced-degree equivalency is unclear",
                evidence=advanced_requirement.group(0),
            )
        return EligibilityResult(
            status="blocked",
            reason="an unconfirmed advanced degree is required",
            evidence=advanced_requirement.group(0),
        )

    if research_title and not _BACHELOR_PATH.search(normalized_jd):
        return EligibilityResult(
            status="pending",
            reason="research qualification fit is unclear",
            evidence=job.title,
        )

    graduation_years = {
        int(next(group for group in match.groups() if group))
        for match in _GRADUATION_CONTEXT.finditer(jd)
    }
    if graduation_years and graduation_years.isdisjoint({2026, 2027}):
        return EligibilityResult(
            status="blocked",
            reason="required graduation window does not include 2026 or 2027",
            evidence=", ".join(str(year) for year in sorted(graduation_years)),
        )

    enrollment: EnrollmentEligibility = (
        "uncertain"
        if classify_role_type(job.title, jd) in {"intern", "coop"}
        and _RETURN_TO_SCHOOL.search(jd)
        else "not_applicable"
    )
    return EligibilityResult(status="apply", enrollment_eligibility=enrollment)


def _required_research_credential(jd_text: str) -> re.Match[str] | None:
    """Return a non-preferred research-track-record requirement."""
    for match in _RESEARCH_CREDENTIAL.finditer(jd_text):
        before = jd_text[: match.start()]
        after = jd_text[match.end() : match.end() + 100]
        preferred_sections = list(_PREFERRED_SECTION_MARKER.finditer(before))
        required_sections = list(_REQUIRED_QUALIFICATION_MARKER.finditer(before))
        last_preferred = preferred_sections[-1].start() if preferred_sections else -1
        last_required = required_sections[-1].start() if required_sections else -1
        local_before = re.split(r"[.;]", before[-180:])[-1]
        local_after = re.split(r"[.;]", after)[0]
        if (
            last_preferred > last_required
            or _PREFERRED_MARKER.search(local_before)
            or _PREFERRED_MARKER.search(local_after)
        ):
            continue
        return match
    return None


def _required_advanced_degree(jd_text: str) -> re.Match[str] | None:
    """Return an advanced degree stated directly or in a required section."""
    if direct := _ADVANCED_DEGREE_REQUIRED.search(jd_text):
        before = jd_text[: direct.start()]
        preferred_sections = list(_PREFERRED_SECTION_MARKER.finditer(before))
        required_sections = list(_REQUIRED_QUALIFICATION_MARKER.finditer(before))
        last_preferred = preferred_sections[-1].start() if preferred_sections else -1
        last_required = required_sections[-1].start() if required_sections else -1
        if last_preferred <= last_required:
            return direct
    for match in _ADVANCED_DEGREE_MENTION.finditer(jd_text):
        before = jd_text[: match.start()]
        preferred_sections = list(_PREFERRED_SECTION_MARKER.finditer(before))
        required_sections = list(_REQUIRED_QUALIFICATION_MARKER.finditer(before))
        last_preferred = preferred_sections[-1].start() if preferred_sections else -1
        last_required = required_sections[-1].start() if required_sections else -1
        if last_required > last_preferred:
            return match
        local_before = jd_text[max(0, match.start() - 120) : match.start()]
        if last_preferred <= last_required and _DEGREE_ASSERTION_MARKER.search(
            local_before
        ):
            return match
    return None


__all__ = [
    "EligibilityResult",
    "EligibilityStatus",
    "EnrollmentEligibility",
    "evaluate_eligibility",
]
