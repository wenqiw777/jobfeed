"""Conservative attribution: a publisher or matching title is not an employer."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from jobfeed.domain.intermediary import intermediary_posting, matches_official
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.real_job_evaluation import select_real_job_input

BODY = "Acme engineers build distributed payment services. " + " ".join(
    f"Implement requirement{i} with database transaction{i} and service{i} monitoring."
    for i in range(25)
)


AUDITED_PUBLISHERS = [
    "TalentHop",
    "Sundayy",
    "Netrolynx AI",
    "FetchJobs.co",
    "Ladders",
    "The Ladders",
    "Jobgether",
    "Wiraa",
    "Jack & Jill",
    (
        "Underdog: Verified engineers. Interview-ready. Searching "
        "confidentially. And companies apply to you"
    ),
    (
        "Underdog.io -Apply to top tech jobs in 60 seconds. "
        "A place where companies apply to you"
    ),
    "Dex",
    "TalentAlly",
    "CodeRound AI",
    "Haystack",
    "hackajob",
    "Hire Feed",
    "Jobverse.io",
    "Underdog.io",
]


def posting(**changes):
    base = JobPosting(
        id="1",
        platform="jobright",
        canonical_id="one",
        url="https://www.dice.com/job-detail/one",
        company="Dice",
        title="Software Engineer",
        location="Boston, MA",
        jd_text=BODY,
        jd_quality=QualityBand.FULL,
        discovered_at=datetime.now(UTC),
    )
    return replace(base, **changes)


def official(**changes):
    return replace(
        posting(
            id="2",
            platform="greenhouse",
            canonical_id="123",
            company="Acme",
            url="https://boards.greenhouse.io/acme/jobs/123",
        ),
        **changes,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"company": "Haystack", "url": "https://www.linkedin.com/jobs/view/1"},
        {"company": "Jobverse.io"},
        {"company": " YARA  AI ", "url": "https://www.linkedin.com/jobs/view/1"},
        {"company": "RemoteHunter", "url": "https://www.linkedin.com/jobs/view/1"},
        {"company": "Remote Hunter", "url": "https://jobright.ai/jobs/info/1"},
        {"company": "Torentify", "url": "https://jobright.ai/jobs/info/1"},
        {"company": "Actual Employer"},
        {
            "url": "https://jobright.ai/jobs/info/1",
            "apply_url": "https://www.dice.com/job-detail/one",
        },
    ],
)
def test_intermediary_is_source_independent(changes):
    assert intermediary_posting(posting(**changes))


def test_unresolved_source_cannot_be_selected_but_official_can():
    assert select_real_job_input("1", [posting()], now=datetime.now(UTC)) is None
    assert (
        select_real_job_input(
            "1", [posting(), official()], now=datetime.now(UTC)
        ).source_job_id
        == "2"
    )


def test_strong_match_and_missing_eligibility_paragraph():
    assert matches_official(
        posting(), official(jd_text=BODY + "\nUS citizenship is required.")
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"company": "OtherCo"},
        {"title": "Staff Engineer"},
        {"location": "Austin, TX"},
        {"jd_text": "Unrelated role " * 200},
        {"closed_at": datetime.now(UTC)},
    ],
)
def test_reject_wrong_or_closed_official(changes):
    target = replace(official(), **changes)
    assert not matches_official(posting(), target)


def test_substring_company_and_spoofed_ats_are_not_proof():
    assert not matches_official(
        posting(jd_text=BODY.replace("Acme", "NotAcme")), official()
    )
    assert not matches_official(
        posting(), official(url="https://evil.test/job?gh_jid=123")
    )


def test_explicit_official_id_precedes_content_but_conflicting_id_is_rejected():
    source = posting(apply_url=official().url, jd_text="A short summary")
    assert matches_official(source, official())
    assert not matches_official(
        posting(apply_url="https://boards.greenhouse.io/acme/jobs/999"), official()
    )


def test_official_extra_text_is_allowed_but_short_generic_excerpt_is_not():
    extra = " ".join(
        f"Additional benefit{i} includes detailed condition{i}." for i in range(15)
    )
    assert matches_official(posting(), official(jd_text=BODY + extra))
    assert not matches_official(posting(jd_text="Acme engineer " * 100), official())


@pytest.mark.parametrize(
    "company",
    [
        "Yara International",
        "Hunter Engineering Company",
        "Tenstorrent",
        "Dice Therapeutics",
    ],
)
def test_similar_employer_names_remain_eligible(company):
    assert not intermediary_posting(official(company=company))


@pytest.mark.parametrize("company", AUDITED_PUBLISHERS)
def test_audited_publishers_cannot_supply_scoring_input(company):
    source = official(company=company)
    assert intermediary_posting(source)
    assert select_real_job_input("1", [source], now=datetime.now(UTC)) is None


@pytest.mark.parametrize(
    "company", ["Underdog", "Haystack Oncology", "Dex Imaging", "Jobright.ai"]
)
def test_other_companies_keep_official_scoring_input(company):
    source = official(company=company)
    assert not intermediary_posting(source)
    assert select_real_job_input("1", [source], now=datetime.now(UTC)) is not None
