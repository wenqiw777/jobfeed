"""Regression cases for the observed v3 inference errors, without model calls."""

# ruff: noqa: RUF001, PLR2004 -- literal source punctuation and expected values.

from scripts.jd_semantic_contract import (
    Document,
    Evidence,
    Qualification,
    SalaryOutput,
    normalize_qualifications,
    resolve_evidence,
    salary_attribute_supported,
    validate_grounding,
)


def document(text, title="Software Engineer"):
    return Document(
        job_id="fixture",
        url="https://example.test/job",
        title=title,
        text=text,
        completeness="complete",
    )


def salary_output(interval="year", interval_quote="base salary"):
    def proof(quote):
        return [{"quote": quote, "block_id": "body"}] if quote else []

    return SalaryOutput.model_validate(
        {
            "schema_version": "2",
            "state": "present",
            "records": [
                {
                    "minimum": 122000,
                    "maximum": 170000,
                    "currency": "CAD",
                    "interval": interval,
                    "kind": "base",
                    "location": "Canada",
                    "level": "unspecified",
                    "state": "present",
                    "evidence": proof("Canada base salary: CAD 122,000–170,000"),
                    "attributes": {
                        "minimum": proof("122,000"),
                        "maximum": proof("170,000"),
                        "currency": proof("CAD"),
                        "interval": proof(interval_quote),
                        "kind": proof("base salary"),
                        "location": proof("Canada"),
                        "level": [],
                    },
                }
            ],
        }
    )


def test_existing_quote_is_not_automatically_evidence_of_yearly_salary():
    output = salary_output()
    issues = validate_grounding(
        output, document("Canada base salary: CAD 122,000–170,000")
    )
    assert "salary.interval.unsupported_value" in issues


def test_unknown_interval_does_not_discard_explicit_amount_and_currency():
    output = salary_output(interval="unknown", interval_quote=None)
    assert (
        validate_grounding(output, document("Canada base salary: CAD 122,000–170,000"))
        == []
    )
    assert output.records[0].minimum == 122000


def test_explicit_period_has_its_own_source_evidence():
    output = salary_output(interval_quote="per year")
    assert (
        validate_grounding(
            output, document("Canada base salary: CAD 122,000–170,000 per year")
        )
        == []
    )


def test_numeric_evidence_must_support_the_actual_value():
    output = salary_output(interval="unknown", interval_quote=None)
    output.records[0].minimum = 222000
    assert "salary.minimum.unsupported_value" in validate_grounding(
        output, document("Canada base salary: CAD 122,000–170,000")
    )


def test_currency_requires_currency_evidence_not_an_unqualified_dollar_sign():
    output = salary_output(interval="unknown", interval_quote=None)
    output.records[0].attributes.currency = [Evidence(quote="$", block_id="body")]
    issues = validate_grounding(
        output, document("Canada base salary: CAD 122,000–170,000 ($)")
    )
    assert "salary.currency.unsupported_value" in issues


def test_title_evidence_has_a_distinct_source_location():
    doc = document("Build systems.", title="Senior Software Engineer")
    resolved = resolve_evidence(Evidence(quote="Senior", block_id="title"), doc)
    assert resolved == {
        "block_id": "title",
        "start": 0,
        "end": 6,
        "source_quote": "Senior",
    }
    assert resolve_evidence(Evidence(quote="Senior", block_id="body"), doc) is None


def test_explicit_bonus_modifiers_are_not_rejected_as_unsupported():
    assert salary_attribute_supported("kind", "bonus", ["sales bonuses"])
    assert salary_attribute_supported("kind", "bonus", ["company bonus"])
    assert not salary_attribute_supported("kind", "base", ["company bonus"])


def test_unsupported_numeric_locale_is_rejected_not_silently_rescaled():
    assert not salary_attribute_supported("minimum", 150, ["1,50"])
    assert salary_attribute_supported("minimum", 123456.5, ["123,456.50"])
    assert salary_attribute_supported("minimum", 125000, ["125K"])


def test_user_college_normalization_preserves_the_extracted_fact():
    text = (
        "College or university graduated, "
        "major in computer science or IT or equivalent."
    )
    raw = {
        "schema_version": "2",
        "state": "present",
        "coverage": dict.fromkeys(
            (
                "degree",
                "experience",
                "graduation",
                "enrollment",
                "start_date",
                "seniority",
                "skill",
                "domain",
            ),
            "not_stated",
        ),
        "facts": [
            {
                "id": "d",
                "kind": "degree",
                "state": "present",
                "modality": "required",
                "subject": "candidate",
                "predicate": "exists",
                "value": "college_university_graduate",
                "unit": "none",
                "scope": "education",
                "as_of": None,
                "experience_domain": None,
                "internship_policy": "unspecified",
                "post_degree": "unspecified",
                "evidence": [{"quote": text, "block_id": "body"}],
            }
        ],
        "required": {"op": "fact", "ref": "d", "children": []},
        "preferred": {"op": "all_of", "ref": None, "children": []},
    }
    raw["coverage"]["degree"] = "present"
    output = Qualification.model_validate(raw)
    normalized = normalize_qualifications(output, document(text))
    assert normalized == [
        {
            "fact_id": "d",
            "value": "bachelor",
            "basis": "user_policy",
            "rule": "reviewed_college_university_graduate",
        }
    ]
    assert output.facts[0].value == "college_university_graduate"


def test_unsupported_college_statement_does_not_get_policy_normalization():
    # The rule is not a global declaration about all colleges or all diplomas.
    assert (
        normalize_qualifications(
            Qualification.model_validate(
                {
                    "schema_version": "2",
                    "state": "not_stated",
                    "facts": [],
                    "coverage": dict.fromkeys(
                        (
                            "degree",
                            "experience",
                            "graduation",
                            "enrollment",
                            "start_date",
                            "seniority",
                            "skill",
                            "domain",
                        ),
                        "not_stated",
                    ),
                    "required": {"op": "all_of", "ref": None, "children": []},
                    "preferred": {"op": "all_of", "ref": None, "children": []},
                }
            ),
            document("Our founder graduated from college."),
        )
        == []
    )
