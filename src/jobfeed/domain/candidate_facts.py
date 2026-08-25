"""Deterministic, evidence-backed candidate facts derived from a resume."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.domain.ml_features import extract_features

_MONTHS_PER_YEAR = 12
_WORK_SECTION = re.compile(
    r"(?:^|\n)\s*(?:WORK\s+EXPERIENCE|EXPERIENCE)\s*\n(?P<body>.*?)"
    r"(?:\n\s*PROJECTS?\s*\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_EDUCATION_SECTION = re.compile(
    r"(?:^|\n)\s*EDUCATION\s*\n(?P<body>.*?)(?:\n\s*(?:TECHNICAL\s+SKILLS|SKILLS|"
    r"WORK\s+EXPERIENCE|EXPERIENCE|PROJECTS?)\s*\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_PROJECT_SECTION = re.compile(
    r"(?:^|\n)\s*PROJECTS?\s*\n(?P<body>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_DATE_RANGE = re.compile(
    r"(?P<start_month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+(?P<start_year>20\d{2})\s*[-\u2013\u2014]\s*"
    r"(?:(?P<end_month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+(?P<end_year>20\d{2})|(?P<present>Present|Current))",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+(?P<year>20\d{2})",
    re.IGNORECASE,
)
_MONTH_NUMBER = {
    name: index
    for index, name in enumerate(
        (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ),
        start=1,
    )
}
_LEVEL_ALIASES = (
    (re.compile(r"\b(intern|new[\s_-]?grad(?:uate)?)\b", re.IGNORECASE), 0),
    (re.compile(r"\b(entry|junior|jr\.?|associate)\b", re.IGNORECASE), 1),
    (re.compile(r"\b(mid|intermediate|engineer\s+ii)\b", re.IGNORECASE), 2),
    (re.compile(r"\b(senior|sr\.?|staff|principal)\b", re.IGNORECASE), 3),
    (re.compile(r"\b(lead|manager|director)\b", re.IGNORECASE), 4),
)
_EVIDENCE_DOMAIN_PATTERNS = (
    (
        "ai_ml",
        re.compile(
            r"\b(?:LLMs?|RAG|machine\s+learning|AI\s+agents?|LangGraph|LangChain)\b",
            re.IGNORECASE,
        ),
    ),
    ("backend", re.compile(r"\b(?:backend|microservices?|REST\s+APIs?)\b", re.I)),
    (
        "distributed",
        re.compile(
            r"\b(?:distributed\s+systems?|event-driven|message\s+queues?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cloud",
        re.compile(
            r"\b(?:AWS|GCP|Azure|serverless|Lambda|DynamoDB|CloudWatch)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ecommerce",
        re.compile(
            r"\b(?:e[\s-]?commerce|price\s+monitoring|product\s+prices?)\b",
            re.IGNORECASE,
        ),
    ),
    ("telecom", re.compile(r"\b(?:telecommunications?|telecom)\b", re.I)),
)


@dataclass(frozen=True, kw_only=True)
class CandidateScoringProfile:
    """Stable facts used by deterministic evaluation components."""

    actual_level: str
    professional_months: int
    internship_months: int
    degree_level: str
    degree_status: str
    graduation_month: str | None
    domain_tags: tuple[str, ...] = ()

    @classmethod
    def from_resume(
        cls,
        resume_text: str,
        *,
        as_of: datetime | None = None,
    ) -> CandidateScoringProfile:
        """Extract timeline, education, and evidenced domains once.

        Args:
            resume_text: Plain-text resume content.
            as_of: Optional reference time for open-ended roles and education.

        Returns:
            Stable candidate facts for deterministic scoring.
        """
        reference = as_of or datetime.now(UTC)
        professional, internship = _experience_months(resume_text, reference)
        education = _section_text(_EDUCATION_SECTION, resume_text)
        degree = extract_features("", education).degree_required
        graduation = _graduation_month(education)
        return cls(
            actual_level=_actual_level(
                resume_text,
                professional,
            ),
            professional_months=professional,
            internship_months=internship,
            degree_level=degree,
            degree_status=_degree_status(degree, graduation, reference),
            graduation_month=graduation,
            domain_tags=_domain_tags(_experience_evidence(resume_text)),
        )


def _candidate_level_rank(profile: CandidateScoringProfile) -> int:
    """Map the candidate's explicit career band to a stable ordinal."""
    for pattern, rank in _LEVEL_ALIASES:
        if pattern.search(profile.actual_level):
            return rank
    return 1


def _domain_tags(text: str) -> tuple[str, ...]:
    """Return legacy verticals plus evidence-oriented software domains."""
    tags = list(extract_features("", text).domain_tags)
    for name, pattern in _EVIDENCE_DOMAIN_PATTERNS:
        if name not in tags and pattern.search(text):
            tags.append(name)
    return tuple(tags)


def _experience_months(resume_text: str, as_of: datetime) -> tuple[int, int]:
    section = _section_text(_WORK_SECTION, resume_text)
    professional: set[int] = set()
    internship: set[int] = set()
    for line in section.splitlines():
        date_match = _DATE_RANGE.search(line)
        if date_match is None:
            continue
        target = (
            internship
            if re.search(r"\bintern(?:ship)?\b", line, re.IGNORECASE)
            else professional
        )
        target.update(_range_month_keys(date_match, as_of))
    return len(professional), len(internship)


def _range_month_keys(match: re.Match[str], as_of: datetime) -> set[int]:
    start_month = _MONTH_NUMBER[match.group("start_month")[:3].lower()]
    start_year = int(match.group("start_year"))
    if match.group("present"):
        end_year, end_month = as_of.year, as_of.month
    else:
        end_year = int(match.group("end_year"))
        end_month = _MONTH_NUMBER[match.group("end_month")[:3].lower()]
    start = start_year * _MONTHS_PER_YEAR + start_month - 1
    end = end_year * _MONTHS_PER_YEAR + end_month - 1
    return set(range(start, end + 1)) if end >= start else set()


def _section_text(pattern: re.Pattern[str], resume_text: str) -> str:
    match = pattern.search(resume_text)
    return match.group("body") if match else resume_text


def _experience_evidence(resume_text: str) -> str:
    matches = (
        _WORK_SECTION.search(resume_text),
        _PROJECT_SECTION.search(resume_text),
    )
    bodies = [match.group("body") for match in matches if match is not None]
    return "\n".join(bodies) if bodies else resume_text


def _graduation_month(education_text: str) -> str | None:
    values = [
        (int(match.group("year")), _MONTH_NUMBER[match.group("month")[:3].lower()])
        for match in _MONTH_YEAR.finditer(education_text)
    ]
    if not values:
        return None
    year, month = max(values)
    return f"{year:04d}-{month:02d}"


def _degree_status(degree: str, graduation: str | None, as_of: datetime) -> str:
    if degree == "none":
        return "none"
    if graduation is None:
        return "unknown"
    return "in_progress" if graduation > as_of.strftime("%Y-%m") else "completed"


def _actual_level(
    resume_text: str,
    professional_months: int,
) -> str:
    work_text = _section_text(_WORK_SECTION, resume_text)
    for pattern, rank in reversed(_LEVEL_ALIASES[2:]):
        if pattern.search(work_text):
            return {2: "mid", 3: "senior", 4: "lead"}[rank]
    if professional_months >= 24:  # noqa: PLR2004 - career-band boundary
        return "mid"
    # Student/new-grad eligibility is a separate fact from professional level.
    # A candidate with evidenced non-intern work is still an entry-level
    # professional while completing a degree; treating that as a lower career
    # rank incorrectly penalizes ordinary Entry/Junior roles.
    return "entry"


__all__ = ["CandidateScoringProfile"]
