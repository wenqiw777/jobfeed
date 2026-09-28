"""Tests for the capability-first JD ingestion demo."""

import json

from scripts.demo_jd_source_capabilities import (
    extract_ashby_job,
    extract_embedded_job_json,
    extract_json_ld,
    extract_lever_job,
    extract_structured_html,
)


def test_ashby_preserves_salary_and_equity_as_separate_native_facts() -> None:
    expected_minimum = 100_000
    result = extract_ashby_job(
        {
            "title": "Engineer",
            "descriptionPlain": "Build systems",
            "compensation": {
                "summaryComponents": [
                    {
                        "compensationType": "Salary",
                        "minValue": 100000,
                        "maxValue": 140000,
                        "currencyCode": "USD",
                        "interval": "1 YEAR",
                    },
                    {
                        "compensationType": "EquityCashValue",
                        "minValue": None,
                        "maxValue": None,
                        "currencyCode": "USD",
                        "interval": "1 YEAR",
                    },
                ]
            },
        },
        "https://api.ashbyhq.com/example",
    )

    assert result.compensation_state == "present"
    assert [fact.kind for fact in result.compensation] == ["Salary", "EquityCashValue"]
    assert result.compensation[0].minimum == expected_minimum


def test_lever_uses_salary_range_without_parsing_description_text() -> None:
    expected_maximum = 120_000
    result = extract_lever_job(
        {
            "text": "Engineer",
            "descriptionPlain": "Competitive compensation",
            "salaryRange": {
                "min": 90000,
                "max": 120000,
                "currency": "USD",
                "interval": "per-year-salary",
            },
            "lists": [
                {
                    "text": "Requirements",
                    "content": "<ul><li>BS or equivalent</li></ul>",
                }
            ],
        },
        "https://api.lever.co/example",
    )

    assert result.compensation[0].maximum == expected_maximum
    assert result.sections["Requirements"] == ["BS or equivalent"]


def test_missing_compensation_stays_unknown_instead_of_becoming_zero() -> None:
    result = extract_lever_job(
        {
            "text": "Engineer",
            "descriptionPlain": "Build systems",
        },
        "https://api.lever.co/example",
    )

    assert result.compensation == []
    assert result.compensation_state == "requires_semantic_review"


def test_embedded_hydration_json_keeps_minimum_and_preferred_separate() -> None:
    payload = {
        "loaderData": {
            "jobDetails": {
                "jobsData": {
                    "postingTitle": "Software Engineer",
                    "minimumQualifications": "BS or equivalent\nOne year experience",
                    "preferredQualifications": "MS preferred",
                }
            }
        }
    }
    encoded = json.dumps(json.dumps(payload))
    html = f"<script>window.STATE = JSON.parse({encoded});</script>"

    result = extract_embedded_job_json(html, "https://company.example/job")

    assert result is not None
    assert result.source_kind == "embedded_job_json"
    assert result.sections["minimumQualifications"] == [
        "BS or equivalent",
        "One year experience",
    ]
    assert result.sections["preferredQualifications"] == ["MS preferred"]
    assert result.compensation_state == "requires_semantic_review"


def test_json_ld_salary_is_native_structured_fact() -> None:
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Engineer",
        "description": "<p>Build systems</p>",
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {"minValue": 10, "maxValue": 20, "unitText": "HOUR"},
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'

    result = extract_json_ld(html, "https://company.example/job")

    assert result is not None
    assert result.compensation_state == "present"
    assert result.compensation[0].interval == "HOUR"


def test_html_fallback_preserves_sections_but_does_not_invent_salary_fact() -> None:
    html = """
    <h1>Engineer</h1>
    <h2>Basic Qualifications</h2><ul><li>BS in CS</li></ul>
    <h2>Preferred Qualifications</h2><p>MS preferred</p>
    <p>The salary range is $100,000 to $140,000 annually.</p>
    """

    result = extract_structured_html(html, "https://company.example/job")

    assert result.sections["Basic Qualifications"] == ["BS in CS"]
    assert result.sections["Preferred Qualifications"] == [
        "MS preferred",
        "The salary range is $100,000 to $140,000 annually.",
    ]
    assert result.compensation == []
    assert result.compensation_state == "text_present_unparsed"
    assert "$100,000" in result.compensation_text_evidence[0]
