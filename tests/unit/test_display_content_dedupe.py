"""Display equivalence is broader than native posting identity."""

# ruff: noqa: PLR2004, RUF001

from datetime import UTC, datetime

from jobfeed.domain import display_content
from jobfeed.domain.dedupe import cluster_twins, pick_display_representatives
from jobfeed.domain.models import JobPosting

BODY = (
    "Build distributed services for satellite communications. "
    "Own testing, deployment and monitoring of production software. "
    "Collaborate with engineers to improve service reliability and performance. "
    "Basic qualifications: Bachelor's degree and 2 years of Python experience. "
)


def posting(identifier: str, platform: str = "linkedin", **kwargs) -> JobPosting:
    values = {
        "id": identifier,
        "platform": platform,
        "canonical_id": identifier,
        "url": f"https://example.com/{identifier}",
        "external_identity": f"{platform}:{identifier}",
        "company": "Amazon",
        "title": "Software Development Engineer – Amazon Leo (US)",
        "location": "Sunnyvale, CA",
        "jd_text": BODY,
        "discovered_at": datetime(2026, 9, 19, tzinfo=UTC),
    }
    values.update(kwargs)
    return JobPosting(**values)


def test_content_copies_fold_across_sources_without_merging_native_ids():
    jobs = [posting("1"), posting("2", "jobright"), posting("3", "handshake")]
    assert len(cluster_twins(jobs)) == 3
    assert len(pick_display_representatives(jobs, {})) == 1


def test_location_only_copies_fold_with_unknown_employer_metadata():
    jobs = [
        posting("1", company="Unknown", jd_text=BODY + " Location: Sunnyvale, CA."),
        posting(
            "2",
            company="Unknown",
            location="San Diego, CA",
            jd_text=BODY + " Location: San Diego, CA.",
        ),
    ]
    assert len(pick_display_representatives(jobs, {})) == 1


def test_applied_content_copy_wins():
    jobs = [posting("1"), posting("2", "handshake")]
    assert pick_display_representatives(jobs, {"2": "applied"}) == [jobs[1]]


def test_distinct_employers_titles_requirements_and_missing_bodies_stay_separate():
    original = posting("1")
    for changed in [
        posting("2", company="Other Company"),
        posting("2", title=original.title + " - Defense"),
        posting("2", jd_text=BODY.replace("2 years", "3 years")),
        posting("2", jd_text=BODY.replace("Python", "Java")),
        posting("2", jd_text=None),
        posting("2", jd_text="Apply now"),
    ]:
        assert len(pick_display_representatives([original, changed], {})) == 2


def test_formatting_copies_fold():
    jobs = [posting("1"), posting("2", jd_text=BODY.replace(". ", ".\n\n"))]
    assert len(pick_display_representatives(jobs, {})) == 1


def test_near_identical_punctuation_copy_folds():
    jobs = [posting("1"), posting("2", jd_text=BODY.replace("testing,", "testing;"))]
    assert len(pick_display_representatives(jobs, {})) == 1


def test_unknown_company_cannot_bridge_two_different_employers():
    jobs = [
        posting("1", company="Unknown"),
        posting("2"),
        posting("3", company="Other"),
    ]
    assert len(pick_display_representatives(jobs, {})) == 2


def test_programming_language_symbols_are_substantive():
    jobs = [
        posting("1", jd_text=BODY.replace("Python", "C++")),
        posting("2", jd_text=BODY.replace("Python", "C#")),
    ]
    assert len(pick_display_representatives(jobs, {})) == 2


def test_near_copy_edit_and_small_requirement_edit_are_distinguished():
    body = " ".join(f"We develop reliable component{i}." for i in range(250)) + BODY
    original = posting("1", jd_text=body)
    cosmetic = posting("2", jd_text=body.replace("develop", "build", 1))
    requirement = posting("3", jd_text=body.replace("Python", "Java"))
    assert len(pick_display_representatives([original, cosmetic], {})) == 1
    assert len(pick_display_representatives([original, requirement], {})) == 2


def test_tiny_team_change_is_not_cosmetic():
    body = " ".join(f"We develop component{i}." for i in range(250))
    jobs = [
        posting("1", jd_text=body + " Our team owns payments."),
        posting("2", jd_text=body + " Our team owns identity."),
    ]
    assert len(pick_display_representatives(jobs, {})) == 2


def test_employer_alias_and_heading_layout_do_not_split_same_jd():
    shared = (BODY + " We build clinical APIs and data pipelines. ") * 5
    jobs = [
        posting(
            "1",
            company="Commure + Athelas",
            jd_text=shared + "\nAI & Agents:\nBuild reliable workflows for clinicians.",
        ),
        posting(
            "2",
            company="Commure",
            jd_text=shared + "\nAI & Agents: Build reliable workflows for clinicians.",
        ),
    ]
    assert display_content.same_display_content(*jobs)


def test_required_language_change_stays_distinct_when_both_languages_appear_elsewhere():
    shared = "We maintain Python and Java services for clinicians. " * 60
    jobs = [
        posting("1", jd_text=shared + "Required: Python experience."),
        posting("2", jd_text=shared + "Required: Java experience."),
    ]
    assert not display_content.same_display_content(*jobs)


def test_distinct_employers_do_not_trigger_pairwise_content_comparison(monkeypatch):
    original = display_content._same_prepared
    calls = []

    def counted(left, right):
        calls.append((left.job.id, right.job.id))
        return original(left, right)

    monkeypatch.setattr(display_content, "_same_prepared", counted)
    jobs = [posting(str(i), company=f"Employer {i}") for i in range(100)]
    assert len(pick_display_representatives(jobs, {})) == len(jobs)
    assert calls == []


def test_body_normalization_is_once_per_posting_during_fold(monkeypatch):
    original = display_content._text
    bodies = []

    def counted(value):
        if len(value) > 200:
            bodies.append(value)
        return original(value)

    monkeypatch.setattr(display_content, "_text", counted)
    jobs = [posting(str(i)) for i in range(20)]
    assert len(pick_display_representatives(jobs, {})) == 1
    assert len(bodies) <= len(jobs)
