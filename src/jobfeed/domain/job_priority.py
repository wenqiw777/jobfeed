"""Pure queue-tier and priority scoring for the confirmed search policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.domain.job_age import effective_job_date
from jobfeed.domain.ml_features import classify_role_type
from jobfeed.domain.models import CompanyIntelligenceMatch, JobPosting
from jobfeed.domain.seniority import classify_seniority_rule

_INTERN_HIGH_FIT = 75
_MAX_PRIMARY_YOE = 3
_PAY_THOUSANDS_CUTOFF = 1000
_MIN_ANNUAL_PAY = 20_000
_MAX_ANNUAL_PAY = 1_000_000
_ESTABLISHED_TEAM_SIZE = 100
_FRESHNESS_RECENT_DAYS = 1
_FRESHNESS_THREE_DAYS = 3
_FRESHNESS_WEEK_DAYS = 7
_FRESHNESS_TWO_WEEKS_DAYS = 14
_NEW_GRAD = re.compile(
    r"\b(?:new[ -]?grad(?:uate)?|university grad(?:uate)?|202[67] grad(?:uate)?)\b",
    re.IGNORECASE,
)
_EARLY = re.compile(
    r"\b(?:early career|entry[ -]?level|junior|jr\.?|engineer\s+I|developer\s+I)\b",
    re.IGNORECASE,
)
_YOE_RANGE = re.compile(
    r"\b(\d{1,2})\s*(?:-|\u2013|\u2014|to)\s*(\d{1,2})\s+years?\b",
    re.IGNORECASE,
)
_PAY_RANGE = re.compile(
    r"\$\s*([0-9][0-9,.]*)(\s*[kK])?\s*(?:-|\u2013|\u2014|to)\s*"
    r"\$?\s*([0-9][0-9,.]*)(\s*[kK])?"
    r"(?:[^\n]{0,35}?\b(per\s+year|annually|annual|per\s+hour|hourly|/\s*(?:yr|year|hr|hour)))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PriorityResult:
    """Queue tier, total priority, and its inspectable components."""

    queue_tier: int
    priority_score: float
    compensation_score: float
    company_strength_score: float
    freshness_score: float
    new_grad_clarity_score: float
    evidence_fit_score: float | None
    role_direction_score: float
    compensation_annual_midpoint: int | None


def priority_for_job(
    job: JobPosting,
    *,
    evidence_fit: int | None,
    company: CompanyIntelligenceMatch | None = None,
    compensation_percentile: float | None = None,
    now: datetime | None = None,
) -> PriorityResult:
    """Calculate one job's tier and within-tier priority score.

    Args:
        job: Posting to rank.
        evidence_fit: Persisted Stage B fit score, if evaluated.
        company: Verified company evidence, if matched.
        compensation_percentile: Pay percentile within the comparable cohort.
        now: Optional clock for deterministic freshness.

    Returns:
        Queue tier, total, and component scores.
    """
    role_type = classify_role_type(job.title, job.jd_text or "")
    is_intern = role_type in {"intern", "coop"}
    evidence_fit_score = float(evidence_fit) if evidence_fit is not None else None
    fit = evidence_fit_score if evidence_fit_score is not None else 50.0
    pay_midpoint = compensation_midpoint(job.jd_text or "")
    compensation = (
        _clamp(compensation_percentile)
        if pay_midpoint is not None and compensation_percentile is not None
        else 50.0
    )
    company_score = company_strength(company)
    freshness = freshness_value(job, now=now)
    clarity = float(new_grad_clarity(job))
    direction = 100.0
    company_comp = compensation * (4 / 7) + company_score * (3 / 7)
    if is_intern:
        tier = 1 if fit >= _INTERN_HIGH_FIT else 2
        total = fit * 0.40 + company_comp * 0.25 + freshness * 0.25 + direction * 0.10
    else:
        tier = 0
        total = (
            compensation * 0.20
            + company_score * 0.15
            + freshness * 0.30
            + clarity * 0.25
            + fit * 0.10
        )
    return PriorityResult(
        queue_tier=tier,
        priority_score=round(total, 2),
        compensation_score=round(compensation, 2),
        company_strength_score=round(company_score, 2),
        freshness_score=round(freshness, 2),
        new_grad_clarity_score=clarity,
        evidence_fit_score=evidence_fit_score,
        role_direction_score=direction,
        compensation_annual_midpoint=pay_midpoint,
    )


def new_grad_clarity(job: JobPosting) -> int:
    """Return the confirmed explicit-career-stage anchor.

    Args:
        job: Posting to classify.

    Returns:
        New Grad clarity score from 40 to 100.
    """
    text = f"{job.title}\n{job.jd_text or ''}"
    if _NEW_GRAD.search(text):
        return 100
    if _EARLY.search(job.title):
        return 85
    ranges = _YOE_RANGE.findall(job.jd_text or "")
    if ranges:
        upper = max(int(high) for _low, high in ranges)
        if upper <= 1:
            return 70
        if upper <= _MAX_PRIMARY_YOE:
            return 55
    decision = classify_seniority_rule(job.title, job.jd_text or "")
    if decision.yoe_min is not None:
        if decision.yoe_min <= 1:
            return 70
        if decision.yoe_min <= _MAX_PRIMARY_YOE:
            return 55
    return 40


def compensation_midpoint(jd_text: str) -> int | None:
    """Extract the largest plausible annualized base-pay range midpoint.

    Args:
        jd_text: Official job-description text.

    Returns:
        Annual midpoint in dollars, or None when absent.
    """
    values: list[int] = []
    for match in _PAY_RANGE.finditer(jd_text):
        trailing = jd_text[match.end() : match.end() + 35]
        unit = f"{match.group(5) or ''} {trailing}".casefold().replace(" ", "")
        try:
            if "hour" in unit or "/hr" in unit:
                low = _raw_pay_number(match.group(1)) * 2080
                high = _raw_pay_number(match.group(3)) * 2080
            else:
                low = _pay_number(match.group(1), match.group(2))
                high = _pay_number(match.group(3), match.group(4))
                if not unit and high < _PAY_THOUSANDS_CUTOFF:
                    continue
        except ValueError:
            continue
        midpoint = int((low + high) / 2)
        if _MIN_ANNUAL_PAY <= midpoint <= _MAX_ANNUAL_PAY:
            values.append(midpoint)
    return max(values) if values else None


def company_strength(company: CompanyIntelligenceMatch | None) -> float:
    """Score only verified company evidence; unknown remains neutral.

    Args:
        company: Merged source evidence, if available.

    Returns:
        Company-strength score from 0 to 100.
    """
    if company is None:
        return 50.0
    if getattr(company, "public_company", False):
        return 90.0
    if company.yc_top_company:
        return 85.0
    if company.team_size is not None and company.team_size >= _ESTABLISHED_TEAM_SIZE:
        return 75.0
    if company.accelerators:
        return 65.0
    if company.confidence == "medium":
        return 60.0
    return 50.0


def freshness_value(job: JobPosting, *, now: datetime | None = None) -> float:
    """Score coarse posting-age buckets without false precision.

    Args:
        job: Posting with source or discovery timestamp.
        now: Optional clock for deterministic scoring.

    Returns:
        Freshness score from 10 to 100.
    """
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    posted = effective_job_date(job, now=reference)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    age_days = max(
        0,
        (reference.astimezone(UTC).date() - posted.astimezone(UTC).date()).days,
    )
    if age_days <= _FRESHNESS_RECENT_DAYS:
        return 100.0
    if age_days <= _FRESHNESS_THREE_DAYS:
        return 85.0
    if age_days <= _FRESHNESS_WEEK_DAYS:
        return 65.0
    if age_days <= _FRESHNESS_TWO_WEEKS_DAYS:
        return 40.0
    return 10.0


def _pay_number(value: str, suffix: str | None) -> float:
    number = _raw_pay_number(value)
    return number * 1000 if suffix or number < _PAY_THOUSANDS_CUTOFF else number


def _raw_pay_number(value: str) -> float:
    return float(value.replace(",", "").rstrip("."))


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


__all__ = [
    "PriorityResult",
    "company_strength",
    "compensation_midpoint",
    "freshness_value",
    "new_grad_clarity",
    "priority_for_job",
]
