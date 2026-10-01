"""Exclude paid AI-data tasks without rejecting ML infrastructure work."""

import pytest

from jobfeed.domain.eligibility import evaluate_eligibility
from jobfeed.domain.filtering import HardFilters, apply_hard_filters
from jobfeed.domain.models import QualityBand
from tests.support.factories import make_job


@pytest.mark.parametrize(
    ("title", "company"),
    [
        ("Machine Learning Engineer - AI Trainer", "DataAnnotation"),
        ("AI Trainer / Study Participant", "Prolific"),
        ("Backend Software Engineer \u2013 AI Trainer", "Example"),
        ("AI-Training Specialist", "Example"),
        ("Data Annotator / Geospatial Annotation Specialist", "Example"),
        ("Data Labeling Specialist", "Example"),
        ("Data Labeller", "Example"),
        ("LLM Response Evaluator", "Example"),
        ("AI Rater", "Example"),
        ("Software Engineer", "DataAnnotation"),
        ("Full Stack Developer", "data annotation"),
    ],
)
def test_ai_data_work_is_a_hard_block_even_with_incomplete_jd(
    title: str, company: str
) -> None:
    job = make_job(title=title, company=company, jd_quality=QualityBand.PARTIAL)

    assert apply_hard_filters(job, HardFilters()) is not None
    result = evaluate_eligibility(job)
    assert result.status == "blocked"
    assert "AI training/data annotation" in (result.reason or "")


@pytest.mark.parametrize(
    "title",
    [
        "Machine Learning Engineer",
        "AI Training Infrastructure Engineer",
        "Software Engineer, Data Annotation Platform",
        "ML Model Evaluation Engineer",
        "Technical Trainer - Software Engineering",
    ],
)
def test_real_engineering_is_not_blocked_by_training_or_annotation_jd(
    title: str,
) -> None:
    job = make_job(
        title=title,
        company="Example DataAnnotation Tools",
        location="Seattle, WA",
        jd_quality=QualityBand.FULL,
        jd_text=(
            "Build software infrastructure for training ML models and data "
            "annotation tools. Support AI trainers and human response evaluators."
        ),
    )
    assert apply_hard_filters(job, HardFilters()) is None
    assert evaluate_eligibility(job).status != "blocked"
