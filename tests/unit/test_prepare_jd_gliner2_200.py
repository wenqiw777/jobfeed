"""Selection checks for the source-aware 200-JD parser cohort."""

from scripts.prepare_jd_gliner2_200 import (
    is_sde_title,
    select_linkedin_cases,
    select_official_cases,
)


def _row(
    row_id,
    *,
    company,
    title="Software Engineer",
    platform="speedyapply",
    enrich_source="speedyapply-workday",
):
    return {
        "id": row_id,
        "company": company,
        "company_norm": company.casefold(),
        "title": title,
        "title_norm": title.casefold(),
        "location_norm": "remote",
        "platform": platform,
        "enrich_source": enrich_source,
        "url": f"https://careers.example/{row_id}",
        "jd_text": (f"Complete job description {row_id} " * 50),
        "discovered_at": f"2026-09-{row_id:02d}",
    }


def test_sde_title_filter_excludes_non_engineering_roles():
    assert is_sde_title("Software Development Engineer, New Grad")
    assert is_sde_title("Junior Full-Stack Developer")
    assert not is_sde_title("Product Manager")
    assert not is_sde_title("Data Scientist")


def test_official_selection_is_unique_by_company_and_excludes_native_api_sources():
    rows = [
        _row(1, company="One"),
        _row(2, company="One", title="Backend Engineer"),
        _row(3, company="Two", enrich_source="api-greenhouse"),
        _row(4, company="Three", enrich_source="jobright_official_jsonld"),
    ]

    selected = select_official_cases(rows, limit=2)

    assert [row["id"] for row in selected] == [1, 4]


def test_official_selection_can_cap_multiple_roles_per_company():
    rows = [
        _row(1, company="One"),
        _row(2, company="One", title="Backend Engineer"),
        _row(3, company="One", title="Frontend Engineer"),
        _row(4, company="Two"),
    ]

    selected = select_official_cases(rows, limit=3, max_per_company=2)

    assert [row["id"] for row in selected] == [1, 2, 4]


def test_linkedin_selection_excludes_jobs_with_an_official_copy():
    linkedin_rows = [
        _row(
            1, company="One", platform="linkedin_guest", enrich_source="linkedin_guest"
        ),
        _row(
            2, company="Two", platform="linkedin", enrich_source="linkedin_detail_pane"
        ),
        _row(
            3, company="Two", platform="linkedin_guest", enrich_source="linkedin_guest"
        ),
    ]
    official_keys = {("one", "software engineer")}

    selected = select_linkedin_cases(linkedin_rows, official_keys, limit=2)

    assert [row["id"] for row in selected] == [2]


def test_linkedin_selection_supports_platform_quotas_and_exclusions():
    rows = [
        _row(1, company="Official", platform="linkedin_guest"),
        _row(2, company="Guest", platform="linkedin_guest"),
        _row(3, company="LinkedIn", platform="linkedin"),
        _row(4, company="JobSpy", platform="linkedin_jobspy"),
    ]

    selected = select_linkedin_cases(
        rows,
        official_keys=set(),
        limit=3,
        excluded_companies={"official"},
        platform_targets={"linkedin_guest": 1, "linkedin": 1, "linkedin_jobspy": 1},
    )

    assert [row["id"] for row in selected] == [2, 3, 4]
