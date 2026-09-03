"""Contract tests for the independent seniority eligibility gate."""

from dataclasses import dataclass

import pytest

from jobfeed.domain.seniority import (
    SCOPE_EXPERIENCE_YEARS,
    SeniorityInput,
    classify_seniority_rule,
)
from jobfeed.services.seniority_gate import HybridSeniorityGate


def test_sub_three_year_minimum_is_in_scope() -> None:
    for requirement in (
        "2+ years of professional experience",
        "1-3 years of software engineering experience",
        "2-4 years of professional experience",
        "minimum 2 years of professional experience",
    ):
        decision = classify_seniority_rule("Software Engineer", requirement)
        assert decision.result == "in_scope"
        assert decision.yoe_min < SCOPE_EXPERIENCE_YEARS  # type: ignore[operator]


def test_three_year_minimum_is_in_scope() -> None:
    for requirement in (
        "3+ years of professional experience",
        "minimum 3 years of professional experience",
        "3-5 years of software engineering experience",
        "At least 3-10+ years working with programming languages",
    ):
        decision = classify_seniority_rule("Software Engineer", requirement)
        assert decision.result == "in_scope"
        assert decision.yoe_min == SCOPE_EXPERIENCE_YEARS


def test_more_than_three_year_minimum_is_out_of_scope() -> None:
    for requirement in (
        "5+ years building production backend systems at scale",
        "at least 4 years of professional experience",
    ):
        decision = classify_seniority_rule("Software Engineer", requirement)
        assert decision.result == "out_of_scope"
        assert decision.yoe_min > SCOPE_EXPERIENCE_YEARS  # type: ignore[operator]


def test_explicit_midlevel_and_senior_titles_are_out_of_scope() -> None:
    for title in (
        "Software Engineer II",
        "Software Engineer 2",
        "Software Engineer III",
        "Mid-Level Software Engineer",
        "Senior Software Engineer",
        "Sr. Backend Engineer",
    ):
        decision = classify_seniority_rule(title, "Build reliable backend services.")
        assert decision.result == "out_of_scope"
        assert decision.reason == "explicit seniority title"


def test_entry_title_wins_over_conflicting_seniority_word() -> None:
    for title in (
        "IT Developer Intern (Senior-Level)",
        "Senior Software Engineer Intern",
        "Software Engineer II, New Graduate",
    ):
        decision = classify_seniority_rule(title, "Build reliable backend services.")
        assert decision.result == "in_scope"
        assert decision.reason == "explicit entry band"


def test_explicit_leadership_scope_is_out_of_scope() -> None:
    title_decision = classify_seniority_rule(
        "Staff Software Engineer", "Build reliable backend services."
    )
    body_decision = classify_seniority_rule(
        "Software Engineer",
        "Own the architecture across teams and set technical direction.",
    )

    assert title_decision.result == "out_of_scope"
    assert title_decision.reason == "explicit senior ownership"
    assert body_decision.result == "out_of_scope"
    assert body_decision.reason == "explicit senior ownership"


def test_preferred_years_do_not_create_a_hard_rejection() -> None:
    decision = classify_seniority_rule(
        "Software Engineer",
        "Required: 2+ years of experience. Preferred: 5+ years of experience.",
    )

    assert decision.result == "in_scope"
    assert decision.yoe_min == SCOPE_EXPERIENCE_YEARS - 1


def test_company_history_is_not_experience_requirement() -> None:
    decision = classify_seniority_rule(
        "Backend Engineer",
        "For more than 50 years, the company has served customers worldwide.",
    )

    assert decision.result == "unclear"
    assert decision.yoe_min is None


def test_entry_band_without_years_is_in_scope() -> None:
    decision = classify_seniority_rule(
        "Software Engineer, New Graduate", "Build customer-facing software."
    )

    assert decision.result == "in_scope"
    assert decision.reason == "explicit entry band"


@dataclass
class _RecordingModel:
    scores: list[float]

    def __post_init__(self) -> None:
        self.seen: list[SeniorityInput] = []

    async def predict_out_of_scope(self, jobs: list[SeniorityInput]) -> list[float]:
        self.seen.extend(jobs)
        return self.scores[: len(jobs)]


@pytest.mark.asyncio
async def test_hybrid_gate_calls_model_only_for_unclear_jobs() -> None:
    model = _RecordingModel(scores=[0.91])
    gate = HybridSeniorityGate(model=model, out_of_scope_threshold=0.8)
    jobs = [
        SeniorityInput("1", "Software Engineer", "2+ years of experience"),
        SeniorityInput("2", "Backend Engineer", "Build backend services."),
        SeniorityInput("3", "Staff Engineer", "Build backend services."),
        SeniorityInput("4", "Senior Software Engineer", "Build backend services."),
        SeniorityInput("5", "Software Engineer II", "Build backend services."),
    ]

    decisions = await gate.predict_batch(jobs)

    assert [decision.result for decision in decisions] == [
        "in_scope",
        "out_of_scope",
        "out_of_scope",
        "out_of_scope",
        "out_of_scope",
    ]
    assert [job.job_id for job in model.seen] == ["2"]
    assert decisions[1].source == "model"
    assert decisions[1].confidence == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_hybrid_gate_without_model_preserves_unclear_result() -> None:
    gate = HybridSeniorityGate(model=None, out_of_scope_threshold=0.8)

    decisions = await gate.predict_batch(
        [SeniorityInput("1", "Backend Engineer", "Build backend services.")]
    )

    assert decisions[0].result == "unclear"
