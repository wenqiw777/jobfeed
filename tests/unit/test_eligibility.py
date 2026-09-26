"""Behavior tests for the confirmed hard-blocker policy."""

from datetime import UTC, datetime

import pytest

from jobfeed.domain.eligibility import evaluate_eligibility
from jobfeed.domain.models import JobPosting, QualityBand


def _job(**overrides: object) -> JobPosting:
    values: dict[str, object] = {
        "platform": "test",
        "canonical_id": "1",
        "url": "https://example.com/1",
        "title": "Software Engineer I",
        "company": "Example",
        "location": "Seattle, WA",
        "discovered_at": datetime(2026, 9, 3, tzinfo=UTC),
        "jd_text": "Build backend software services. Bachelor's degree required.",
        "jd_quality": QualityBand.FULL,
    }
    values.update(overrides)
    return JobPosting(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "status", "reason"),
    [
        ({}, "apply", None),
        ({"jd_quality": QualityBand.PARTIAL}, "pending", "official JD"),
        ({"location": ""}, "pending", "location"),
        ({"location": "Singapore"}, "blocked", "outside the United States"),
        (
            {"jd_text": "Requires at least 4 years of software experience."},
            "blocked",
            "more than 3 years",
        ),
        (
            {"jd_text": "An active TS/SCI clearance is required."},
            "blocked",
            "active clearance",
        ),
        (
            {"jd_text": "Must be able to obtain a Secret clearance after hire."},
            "apply",
            None,
        ),
        (
            {"jd_text": "Applicants must graduate in 2025."},
            "blocked",
            "graduation window",
        ),
        (
            {
                "title": "Software Engineering Intern",
                "jd_text": "Must return to school after the internship.",
            },
            "apply",
            None,
        ),
    ],
)
def test_confirmed_eligibility_boundaries(
    overrides: dict[str, object], status: str, reason: str | None
) -> None:
    result = evaluate_eligibility(_job(**overrides))

    assert result.status == status
    if reason is not None:
        assert reason in result.reason


def test_intern_return_to_school_is_visible_with_uncertain_warning() -> None:
    result = evaluate_eligibility(
        _job(
            title="Software Engineering Intern",
            jd_text="Must return to school after the internship.",
        )
    )

    assert result.status == "apply"
    assert result.enrollment_eligibility == "uncertain"


def test_required_phd_across_qualification_lines_is_blocked() -> None:
    result = evaluate_eligibility(
        _job(
            jd_text=(
                "Qualifications\n"
                "Required:\n"
                "* PhD in Physics, Computer Science, or a related field."
            )
        )
    )

    assert result.status == "blocked"
    assert result.reason == "an unconfirmed advanced degree is required"


def test_preferred_phd_does_not_create_a_degree_block() -> None:
    result = evaluate_eligibility(
        _job(jd_text="Preferred qualifications:\n* PhD in Computer Science.")
    )

    assert result.status == "apply"


def test_required_word_inside_preferred_degree_section_does_not_block() -> None:
    result = evaluate_eligibility(
        _job(
            jd_text=(
                "Preferred qualifications:\n"
                "* PhD required for applicants to the research-focused track."
            )
        )
    )

    assert result.status == "apply"


def test_required_phd_with_equivalent_path_is_pending() -> None:
    result = evaluate_eligibility(
        _job(jd_text="Qualifications Required:\n* PhD or equivalent experience.")
    )

    assert result.status == "pending"
    assert result.reason == "advanced-degree equivalency is unclear"


def test_required_qualification_section_marks_masters_as_a_blocker() -> None:
    result = evaluate_eligibility(
        _job(
            jd_text=(
                "What we need to see:\n"
                "* Master's degree in Computer Engineering.\n"
                "Ways to stand out:\n* Experience with compilers."
            )
        )
    )

    assert result.status == "blocked"
    assert result.reason == "an unconfirmed advanced degree is required"


@pytest.mark.parametrize(
    "jd_text",
    [
        "Requirements: Employer will accept a Master's degree in Computer Science.",
        "Qualifications PhD in Artificial Intelligence or Data Science.",
        (
            "We build ML systems and list Qualifications: "
            "Advanced degree (MS or PhD) in Computer Science."
        ),
        "You hold a master\u2019s degree in Engineering or Applied Mathematics.",
        (
            "Who We Are Looking For Currently enrolled in a US university "
            "in a Master\u2019s degree program."
        ),
    ],
)
def test_common_required_degree_phrasings_are_blocked(jd_text: str) -> None:
    result = evaluate_eligibility(_job(jd_text=jd_text))

    assert result.status == "blocked"
    assert result.reason == "an unconfirmed advanced degree is required"


def test_advanced_degree_cohort_in_title_is_blocked() -> None:
    result = evaluate_eligibility(
        _job(
            title="Software Engineer PhD Intern",
            jd_text="Build streaming systems with the research team.",
        )
    )

    assert result.status == "blocked"
    assert result.reason == "an unconfirmed advanced degree is required"


def test_required_section_degree_with_equivalent_path_is_pending() -> None:
    result = evaluate_eligibility(
        _job(
            jd_text=(
                "What we need to see:\n"
                "* Master's or PhD degree in Computer Engineering "
                "(or equivalent experience)."
            )
        )
    )

    assert result.status == "pending"
    assert result.reason == "advanced-degree equivalency is unclear"


def test_research_scientist_with_required_research_record_is_blocked() -> None:
    result = evaluate_eligibility(
        _job(
            title="Research Scientist - Generative AI",
            jd_text=(
                "Minimum qualifications:\n"
                "* PhD or equivalent practical experience.\n"
                "* Outstanding research track record."
            ),
        )
    )

    assert result.status == "blocked"
    assert result.reason == "advanced research credentials are required"


def test_ambiguous_research_scientist_is_pending_not_apply() -> None:
    result = evaluate_eligibility(
        _job(
            title="Applied Research Scientist",
            jd_text="Develop machine-learning systems and conduct applied research.",
        )
    )

    assert result.status == "pending"
    assert result.reason == "research qualification fit is unclear"


def test_research_scientist_with_bachelors_path_and_engineering_work_can_apply() -> (
    None
):
    result = evaluate_eligibility(
        _job(
            title="Applied Research Scientist",
            jd_text=(
                "Minimum qualifications: Bachelor's degree in Computer Science. "
                "Build, test, and deploy production machine-learning services."
            ),
        )
    )

    assert result.status == "apply"


def test_preferred_research_publications_do_not_block_bachelors_path() -> None:
    result = evaluate_eligibility(
        _job(
            title="Applied Research Scientist",
            jd_text=(
                "Minimum qualifications: Bachelor's degree in Computer Science. "
                "Build production ML services. Preferred qualifications: "
                "strong publication record at top-tier conferences."
            ),
        )
    )

    assert result.status == "apply"


def test_explicit_physical_engineering_title_is_out_of_scope() -> None:
    result = evaluate_eligibility(
        _job(
            title="Entry-Level Highway Design Engineer", jd_text="Use design software."
        )
    )

    assert result.status == "blocked"
    assert result.reason == "work is outside the software-related target scope"


def test_foreign_market_title_overrides_bad_structured_us_location() -> None:
    result = evaluate_eligibility(_job(title="Embedded Software Engineer (m/w/d)"))

    assert result.status == "blocked"
    assert "non-US" in result.reason


def test_generic_engineer_with_confirmed_software_jd_is_apply() -> None:
    result = evaluate_eligibility(
        _job(title="Engineer I", jd_text="Build backend services in Python.")
    )

    assert result.status == "apply"


@pytest.mark.parametrize(
    "title",
    [
        "Circuit Design Engineer New Grad",
        "Research Scientist New Grad - Circuits",
        "ASIC Physical Design Engineer New Grad",
        "ASIC Verification Engineer New Grad",
        "RTL Design Engineer",
        "FPGA Engineer I",
        "Electrical Engineer - Early Career",
        "Silicon Design Engineer New Grad",
        "Design Engineer - DRAM Technology and Products",
    ],
)
def test_explicit_hardware_titles_are_outside_software_scope(title: str) -> None:
    result = evaluate_eligibility(_job(title=title, is_swe_role=True))

    assert result.status == "blocked"
    assert result.reason == "work is outside the software-related target scope"


def test_explicit_software_hardware_title_remains_in_scope() -> None:
    result = evaluate_eligibility(
        _job(title="Embedded Software Engineer - FPGA", is_swe_role=True)
    )

    assert result.status == "apply"
    assert result.reason is None


def test_explicit_sde_label_overrides_ambiguous_engineer_title() -> None:
    result = evaluate_eligibility(
        _job(
            title="Entry-Level Water Resources Engineer",
            jd_text="Design stormwater drainage and watershed infrastructure.",
            is_swe_role=False,
        )
    )

    assert result.status == "blocked"
    assert result.reason == "work is outside the software-related target scope"
