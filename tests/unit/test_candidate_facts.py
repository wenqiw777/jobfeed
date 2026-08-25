"""Regression tests for resume-derived candidate facts."""

from datetime import UTC, datetime

from jobfeed.domain.candidate_facts import CandidateScoringProfile

_PROFESSIONAL_MONTHS = 6
_INTERNSHIP_MONTHS = 4


def test_student_with_professional_work_has_entry_professional_level() -> None:
    resume = """EDUCATION
University of Michigan | Aug 2023 - May 2027
B.S. Computer Engineering

WORK EXPERIENCE
Timing LLC - Software Engineer | Mar 2026 - Present
Built production LLM and backend systems.
AsiaInfo - Software Engineer Intern | May 2025 - Aug 2025
Built telecommunications software.

PROJECTS
Built a cloud price-monitoring service.
"""

    profile = CandidateScoringProfile.from_resume(
        resume,
        as_of=datetime(2026, 8, 25, tzinfo=UTC),
    )

    assert profile.actual_level == "entry"
    assert profile.professional_months == _PROFESSIONAL_MONTHS
    assert profile.internship_months == _INTERNSHIP_MONTHS
    assert profile.degree_status == "in_progress"
    assert profile.graduation_month == "2027-05"
