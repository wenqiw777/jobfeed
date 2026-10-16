"""Regression coverage for title evidence conflicting with low experience."""

import pytest

from jobfeed.domain.seniority import classify_seniority_rule


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Sr. Engineer, AI Platforms",
        "Software Engineer III",
        "SDE 3",
        "SDE III",
        "SWE III",
        "Staff Software Engineer",
        "Principal Engineer",
    ],
)
@pytest.mark.parametrize(
    "jd",
    [
        "Required: 2 years of software engineering experience.",
        "Required: 3\u20138+ years of experience designing backend systems.",
    ],
)
def test_low_minimum_does_not_override_senior_title(title: str, jd: str) -> None:
    assert classify_seniority_rule(title, jd).result == "out_of_scope"


@pytest.mark.parametrize(
    ("title", "jd"),
    [
        (
            "Senior Software Engineer",
            "This position is open to new graduates, at lower job levels.",
        ),
        (
            "Software Engineer I / III",
            "Required: 2 years of software engineering experience.",
        ),
        (
            "Member of Technical Staff",
            "Required: 2 years of software engineering experience.",
        ),
        ("Software Engineer", "Required: 3 years of software engineering experience."),
        ("Software Engineer 2", "0-3 years of software development experience."),
    ],
)
def test_viable_junior_paths_remain_eligible(title: str, jd: str) -> None:
    assert classify_seniority_rule(title, jd).result == "in_scope"


@pytest.mark.parametrize(
    "title",
    [
        "Platform Engineer II",
        "Software Engineer 2",
        "Site Reliability Engineer (Associate, Experienced, or Senior)",
    ],
)
def test_low_year_and_mixed_level_roles_are_preserved(title: str) -> None:
    assert (
        classify_seniority_rule(
            title, "Required: 2 years of software development experience."
        ).result
        == "in_scope"
    )


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer II/Sr. Software Engineer",
        "Software Engineer/Sr. Software Engineer",
        "Software Engineering II and Senior Software Engineer (CoreAI)",
        "Lead Software Engineer/Software Engineer - Digital Measurement",
        "Engineer/Sr Engineer, IT Software",
        "Sr Associate Engineer/Engineer, IT Software",
        "Software Engineer\u2013Developers (Associate / Experienced / Senior)",
    ],
)
def test_mixed_hiring_bands_keep_low_year_path(title: str) -> None:
    assert (
        classify_seniority_rule(
            title, "Required: 2 years of software development experience."
        ).result
        == "in_scope"
    )
