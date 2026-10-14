"""Evidence-based screening must not confuse normal employers with publishers."""

from dataclasses import replace

import pytest

from jobfeed.domain.posting_spam import assess_posting
from tests.support.factories import make_job


def assess(body, **kwargs):
    return assess_posting(
        replace(
            make_job(),
            company="New Publisher",
            title="Software Engineer",
            jd_text=body,
            **kwargs,
        )
    )


@pytest.mark.parametrize(
    "body,category",
    [
        (
            "We are sharing this opportunity on behalf of Acme. Build payment systems.",
            "publisher_redirect",
        ),
        (
            "About The Company\n\nAcme is a payments company.\nAbout The "
            "Role\nBuild software.",
            "publisher_redirect",
        ),
    ],
)
def test_redirect_and_attribution_block(body, category):
    result = assess(body, apply_url="https://www.fetchjobs.co/job/123")
    assert result.disposition == "block"
    assert result.category == category
    assert result.company_evidence
    assert result.evidence


@pytest.mark.parametrize(
    "body",
    [
        "Help train and evaluate AI models. This is a remote, "
        "project-based role. Review code and model outputs.",
        "Software engineers review, author, and evaluate YAML-based "
        "configurations for AI training. Type: Hourly Contract / Task-Based.",
    ],
)
def test_contributor_work_blocks_only_posting(body):
    result = assess(body)
    assert result.disposition == "block"
    assert not result.company_evidence


def test_explicit_training_then_placement():
    assert (
        assess(
            "Start with 12 weeks of paid training, then deploy to an enterprise client."
        ).disposition
        == "block"
    )


def test_template_requires_multiple_specific_placeholders():
    assert (
        assess(
            "Join us as [Job Title], using [specific programming languages]."
        ).disposition
        == "block"
    )
    assert assess("Join us as [Job Title].").disposition == "review"


@pytest.mark.parametrize(
    "body",
    [
        "We build AI training pipelines and annotation tools. "
        "Full-time engineers own production infrastructure.",
        "Contract engineer to build data pipelines for our AI "
        "models. We provide ongoing training.",
        "We do not recruit on behalf of other companies. Build our product.",
        'Our fraud detection system detects text such as "on behalf '
        'of" in job descriptions.',
        "Develop Java code. Training and mentorship available. Work with our clients.",
        "Work on manufacturing software. We train models for robotics.",
    ],
)
def test_normal_engineering_is_clear(body):
    assert assess(body).disposition == "clear"


def test_unnamed_client_is_review_not_company_block():
    result = assess(
        "We are recruiting on behalf of an unnamed client for a Software Engineer."
    )
    assert result.disposition == "review"
    assert not result.company_evidence


def test_host_boundary_and_missing_attribution_do_not_block():
    body = "We are sharing this opportunity on behalf of Acme."
    assert (
        assess(
            body, apply_url="https://fetchjobs.co.attacker.example/job/1"
        ).disposition
        == "review"
    )
    assert (
        assess("Build APIs.", apply_url="https://fetchjobs.co/job/1").disposition
        == "review"
    )


def test_same_employer_intro_is_not_mismatch():
    assert (
        assess(
            "About The Company\n\nNew Publisher is building "
            "products.\nAbout The Role\nDevelop software."
        ).disposition
        == "clear"
    )


def test_infrastructure_with_task_based_systems_and_code_review_is_not_a_gig():
    assert (
        assess(
            "Build task-based orchestration for AI training. "
            "Participate in code review and deploy our production services."
        ).disposition
        == "clear"
    )


def test_negated_placement_and_quoted_templates_do_not_auto_block():
    assert (
        assess(
            "We do not offer paid training, then deploy to an enterprise client."
        ).disposition
        != "block"
    )
    assert (
        assess(
            "Build a template linter that detects [Job Title] and "
            "[specific programming languages] placeholders."
        ).disposition
        != "block"
    )


@pytest.mark.parametrize(
    "intro",
    [
        "New Publisher, the leading platform, is building products.",
        "New Publisher Labs is building products.",
        "We are building products.",
    ],
)
def test_company_descriptions_and_first_person_are_not_mismatches(intro):
    assert assess("About The Company\n\n" + intro).disposition == "clear"


def test_explicit_unknown_job_platform_can_be_identified_without_domain_list():
    result = assess(
        "New Publisher is a job board. "
        "We are sharing this opportunity on behalf of Acme.",
        apply_url="https://new-platform.example/job/42",
    )
    assert result.disposition == "block"
    assert result.company_evidence


def test_job_platforms_internal_engineering_remains_clear():
    assert (
        assess(
            "We are a job board. Build our own search and matching infrastructure."
        ).disposition
        == "clear"
    )


def test_platform_internal_role_not_blocked_by_description_of_customer_business():
    result = assess(
        "We are a job board. Build our internal matching infrastructure. "
        "Our sales team is recruiting on behalf of clients.",
        apply_url="https://meeboss.com/job/42",
    )
    assert result.disposition != "block"


def test_contractor_evaluator_is_not_disguised_as_ordinary_engineering():
    result = assess(
        "As a Software Engineering evaluator, you will create datasets for "
        "training large language models. Work on AI model training initiatives. "
        "Type: Contractor. Duration: 1 month."
    )
    assert result.disposition == "block"
