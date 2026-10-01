"""Explicit non-internship requirements and their viable alternatives."""

import pytest

from jobfeed.domain.seniority import (
    SCOPE_EXPERIENCE_YEARS,
    SeniorityInput,
    classify_seniority_rule,
)
from jobfeed.services.seniority_gate import HybridSeniorityGate


@pytest.mark.parametrize(
    "jd",
    [
        "Basic Qualifications: 3+ years of non-internship "
        "professional software development experience.",
        "Minimum 3 years of non internship software engineering experience.",
        "Required: 3-5 years of non-internship professional software "
        "development experience.",
        "Basic Qualifications: 3+ years of non-internship design or architecture "
        "(design patterns, reliability and scaling) of new and "
        "existing systems experience.",
        "Basic Qualifications: 3+ years of non-internship "
        "professional software development experience "
        "2+ years of non-internship design or architecture (design "
        "patterns, reliability and scaling) "
        "of new and existing systems experience Bachelor's degree or "
        "foreign equivalent.",
    ],
)
def test_required_three_year_non_internship_experience_is_blocked(jd: str) -> None:
    decision = classify_seniority_rule("Software Development Engineer II", jd)
    assert decision.result == "out_of_scope"
    assert decision.yoe_min == SCOPE_EXPERIENCE_YEARS
    assert decision.reason == "minimum non-internship experience is at least 3 years"


@pytest.mark.parametrize(
    ("title", "jd"),
    [
        (
            "Software Engineer",
            "3+ years of professional software development experience.",
        ),
        (
            "Software Engineer",
            "2+ years of non-internship professional software development experience.",
        ),
        (
            "Software Engineer",
            "0-3 years of non-internship software development experience.",
        ),
        (
            "Software Engineer",
            "3+ years of non-internship software development experience preferred.",
        ),
        (
            "Software Engineer",
            "Preferred Qualifications\n3+ years of non-internship "
            "software development experience.",
        ),
        (
            "Software Engineer",
            "Required: Master's degree or 3+ years of non-internship "
            "software development experience.",
        ),
        (
            "Software Engineer",
            "Required: 3+ years of non-internship software development "
            "experience; or a Master's degree.",
        ),
        (
            "Software Engineer",
            "Required: Bachelor's degree with 3+ years of non-internship "
            "software development experience; or a Master's degree with "
            "0 years of experience.",
        ),
        (
            "Software Engineer",
            "Required: 3+ years of non-internship software development "
            "experience, or an equivalent combination of education and "
            "experience.",
        ),
        (
            "Software Engineer",
            "Up to 3 years of non-internship software development experience.",
        ),
        (
            "Software Engineer",
            "3+ years of non-internship software development experience "
            "is not required.",
        ),
        (
            "Software Engineer",
            "We do not require 3 years of non-internship "
            "software development experience.",
        ),
        (
            "Software Engineer",
            "3+ years of non-internship software development experience or "
            "1 year of software development experience.",
        ),
        (
            "Software Engineer",
            "We have 3 years of non-internship software development experience.",
        ),
        (
            "Software Engineer",
            "Required: 2.3 years of non-internship software development experience.",
        ),
        (
            "Software Engineer",
            "Hiring new grads. 3+ years of non-internship software "
            "development experience.",
        ),
        (
            "Software Engineer I / III",
            "Required: 3+ years of non-internship software development experience.",
        ),
        (
            "Software Engineer",
            "Required: 3+ years of software development experience. "
            "Internships are non-internship onboarding topics.",
        ),
    ],
)
def test_non_internship_controls_are_not_blocked(title: str, jd: str) -> None:
    assert classify_seniority_rule(title, jd).result != "out_of_scope"


@pytest.mark.asyncio
async def test_explicit_non_internship_block_bypasses_model() -> None:
    class NoModelCalls:
        async def predict_out_of_scope(
            self, _jobs: list[SeniorityInput]
        ) -> list[float]:
            pytest.fail("explicit non-internship requirement must not reach the model")

    gate = HybridSeniorityGate(model=NoModelCalls(), out_of_scope_threshold=0.9)
    decisions = await gate.predict_batch(
        [
            SeniorityInput(
                "1",
                "Software Engineer",
                "Basic Qualifications: 3+ years of non-internship "
                "professional software development experience.",
            ),
        ]
    )
    assert decisions[0].result == "out_of_scope"
    assert decisions[0].source == "rule"
