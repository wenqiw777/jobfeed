"""Offline contracts; constructed counterexamples, not an accuracy benchmark."""

import pytest
from pydantic import ValidationError

from scripts.jd_semantic_contract import (
    CandidateScenario,
    Document,
    Evidence,
    Fact,
    Node,
    Qualification,
    Salary,
    SalaryOutput,
    Truth,
    combine,
    evaluate,
    finalize,
    salary_selection,
    validate_grounding,
)


def fact(key="a", kind="degree", value="master", **overrides):
    values = {
        "id": key,
        "kind": kind,
        "state": "present",
        "modality": "required",
        "subject": "candidate",
        "predicate": "gte",
        "value": value,
        "unit": "none",
        "scope": "general",
        "as_of": None,
        "experience_domain": None,
        "internship_policy": "unspecified",
        "post_degree": "unspecified",
        "evidence": [Evidence(quote="Requirement", block_id="body")],
    }
    values.update(overrides)
    return Fact(**values)


def leaf(key):
    return Node(op="fact", ref=key, children=[])


def group(op, *nodes):
    return Node(op=op, ref=None, children=list(nodes))


def qualification(facts, root=None, **overrides):
    values = {
        "schema_version": "1",
        "state": "present",
        "facts": facts,
        "required": root
        or group("all_of", *(leaf(f.id) for f in facts if f.modality == "required")),
        "preferred": group(
            "all_of", *(leaf(f.id) for f in facts if f.modality == "preferred")
        ),
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
    }
    for item in facts:
        if item.kind in values["coverage"]:
            values["coverage"][item.kind] = item.state
    values.update(overrides)
    return Qualification(**values)


def candidate(**overrides):
    values = {
        "name": "synthetic",
        "degree": "bachelor",
        "credential_status": "completed",
        "graduation_start": "2026-12-01",
        "graduation_end": "2026-12-31",
    }
    values.update(overrides)
    return CandidateScenario(**values)


def test_and_or_preserves_degree_experience_paths():
    facts = [
        fact("bs", value="bachelor"),
        fact("five", "experience", 5, unit="years", scope="professional"),
        fact("ms"),
        fact("three", "experience", 3, unit="years", scope="professional"),
    ]
    root = group(
        "any_of",
        group("all_of", leaf("bs"), leaf("five")),
        group("all_of", leaf("ms"), leaf("three")),
    )
    output = qualification(facts, root)
    assert evaluate(output, [candidate()]).truth == Truth.FALSE
    assert evaluate(output, [candidate(degree="master")]).truth == Truth.TRUE


def test_required_skill_and_domain_do_not_block_or_remove_or_branch():
    output = qualification(
        [fact("ms"), fact("domain", "domain", "quantum", predicate="exists")],
        group("any_of", leaf("ms"), leaf("domain")),
    )
    assert evaluate(output, [candidate()]).truth == Truth.TRUE


def test_preferred_does_not_become_a_required_leaf():
    preferred = fact(modality="preferred")
    assert evaluate(qualification([preferred]), [candidate()]).truth == Truth.TRUE
    with pytest.raises(ValidationError):
        qualification([preferred], leaf("a"))


def test_missing_and_duplicate_references_rejected():
    with pytest.raises(ValidationError):
        qualification([fact()], leaf("missing"))
    with pytest.raises(ValidationError):
        qualification([fact(), fact()])


def test_required_fact_cannot_be_omitted_from_tree():
    with pytest.raises(ValidationError):
        qualification([fact("a"), fact("b")], leaf("a"))


def test_unknown_numeric_experience_not_zero_or_block():
    output = qualification(
        [
            fact(
                kind="experience",
                value=None,
                predicate="exists",
                unit="years",
                scope="professional",
            )
        ]
    )
    assert evaluate(output, [candidate()]).truth == Truth.UNKNOWN
    assert finalize(output, [candidate()])["status"] == "Apply"
    assert finalize(output, [candidate()])["review_required"]


@pytest.mark.parametrize(
    "predicate,value,expected",
    [("gt", 3, Truth.FALSE), ("gte", 3, Truth.TRUE), ("gt", 5, Truth.FALSE)],
)
def test_experience_comparator_uses_search_policy_not_fabricated_work_history(
    predicate, value, expected
):
    output = qualification(
        [
            fact(
                kind="experience",
                value=value,
                predicate=predicate,
                unit="years",
                scope="professional",
            )
        ]
    )
    assert evaluate(output, [candidate()]).truth == expected


def test_no_degree_required_is_waiver_not_prohibition():
    output = qualification([fact(value=None, predicate="waived", modality="waived")])
    assert evaluate(output, [candidate(degree=None)]).truth == Truth.TRUE


def test_same_graduation_scenario_must_satisfy_all_conditions():
    output = qualification(
        [
            fact("grad", "graduation", "2026-12-31", predicate="lte", unit="date"),
            fact(
                "enrolled",
                "enrollment",
                "not_graduated",
                predicate="eq",
                as_of="2027-03-01",
            ),
        ]
    )
    scenarios = [
        candidate(credential_status="planned"),
        candidate(
            name="may",
            credential_status="planned",
            graduation_start="2027-05-01",
            graduation_end="2027-05-31",
        ),
    ]
    assert evaluate(output, scenarios).truth == Truth.FALSE


def test_month_precision_does_not_invent_graduation_day():
    output = qualification(
        [fact(kind="graduation", value="2026-12-15", predicate="lte", unit="date")]
    )
    assert evaluate(output, [candidate()]).truth == Truth.UNKNOWN


def test_not_yet_graduated_needs_a_reference_date():
    output = qualification(
        [fact(kind="enrollment", value="not_graduated", predicate="eq")]
    )
    assert evaluate(output, [candidate()]).truth == Truth.UNKNOWN


def test_enrollment_exists_is_not_executed_as_equality():
    output = qualification(
        [
            fact(
                kind="enrollment",
                value="not_graduated",
                predicate="exists",
                as_of="2027-09-01",
            )
        ]
    )
    assert finalize(output, [candidate()], review=output)["status"] == "Apply"
    assert finalize(output, [candidate()], review=output)["review_required"]


def test_experience_bounds_must_have_one_shared_value():
    output = qualification(
        [
            fact(
                "min",
                "experience",
                3,
                predicate="gte",
                unit="years",
                scope="professional",
            ),
            fact(
                "max",
                "experience",
                2,
                predicate="lte",
                unit="years",
                scope="professional",
            ),
        ]
    )
    assert evaluate(output, [candidate()]).truth == Truth.UNKNOWN


def test_nested_and_does_not_change_contradictory_experience_to_block():
    facts = [
        fact(
            "min", "experience", 3, predicate="gte", unit="years", scope="professional"
        ),
        fact(
            "max", "experience", 2, predicate="lte", unit="years", scope="professional"
        ),
    ]
    output = qualification(
        facts, group("all_of", leaf("min"), group("all_of", leaf("max")))
    )
    assert evaluate(output, [candidate()]).truth == Truth.UNKNOWN


def test_future_enrollment_after_graduation_stays_uncertain():
    output = qualification(
        [fact(kind="enrollment", value="enrolled", predicate="eq", as_of="2027-09-01")]
    )
    assert finalize(output, [candidate()], review=output)["status"] == "Apply"
    assert finalize(output, [candidate()], review=output)["review_required"]


def test_planned_degree_not_assumed_completed_now():
    output = qualification([fact(value="bachelor")])
    assert (
        evaluate(output, [candidate(credential_status="planned")]).truth
        == Truth.UNKNOWN
    )


def test_block_requires_matching_evidence_and_relation_review():
    output = qualification([fact()])
    assert finalize(output, [candidate()])["status"] == "Pending"
    assert finalize(output, [candidate()], review=output)["status"] == "Blocked"
    different = qualification([fact(value="phd")])
    result = finalize(output, [candidate()], review=different)
    assert result["status"] == "Apply"
    assert result["review_required"]


def test_completed_degree_can_satisfy_unspecified_degree_or_diploma():
    output = qualification(
        [
            fact("degree", value="degree", predicate="exists"),
            fact("diploma", value="diploma", predicate="exists"),
        ],
        group("any_of", leaf("degree"), leaf("diploma")),
    )
    assert evaluate(output, [candidate()]).truth == Truth.TRUE


def test_graduated_without_date_is_a_status_not_a_fabricated_date():
    output = qualification(
        [fact(kind="graduation", value="graduated", predicate="exists")]
    )
    assert evaluate(output, [candidate()]).truth == Truth.TRUE
    assert (
        evaluate(output, [candidate(credential_status="planned")]).truth
        == Truth.UNKNOWN
    )


def test_evidence_allows_only_whitespace_equivalence():
    document = Document(
        job_id="1",
        title="role",
        url="https://example.test/1",
        text="Required\nMaster degree",
        completeness="complete",
    )
    output = qualification(
        [fact(evidence=[Evidence(quote="Required Master degree", block_id="body")])]
    )
    assert validate_grounding(output, document) == []
    output.facts[0].evidence[0].quote = "Preferred Master degree"
    assert validate_grounding(output, document)


def test_partial_source_and_review_failure_never_block():
    output = qualification([fact()])
    assert (
        finalize(output, [candidate()], review=output, complete=False)["status"]
        == "Pending"
    )
    assert finalize(output, [candidate()], review_failed=True)["status"] == "Pending"


def test_grounding_checks_full_exact_quote_and_partial_missing():
    doc = Document(
        job_id="example",
        url="https://example.test/job",
        title="Role",
        text="Requirement",
        completeness="complete",
    )
    assert validate_grounding(qualification([fact()]), doc) == []
    bad = fact(evidence=[Evidence(quote="invented", block_id="body")])
    assert validate_grounding(qualification([bad]), doc)
    missing = qualification([], state="not_stated")
    assert validate_grounding(
        missing, doc.model_copy(update={"completeness": "partial"})
    )


def salary(location="US-NY", **overrides):
    values = {
        "minimum": 100000,
        "maximum": 150000,
        "currency": "USD",
        "interval": "year",
        "kind": "base",
        "location": location,
        "level": "unspecified",
        "state": "present",
        "evidence": [Evidence(quote="Salary", block_id="body")],
    }
    values.update(overrides)
    return Salary(**values)


def test_salary_ranges_are_not_silently_merged_or_selected():
    output = SalaryOutput(
        schema_version="1", state="present", records=[salary(), salary("US-CA")]
    )
    assert salary_selection(output)["state"] == "multiple_ranges"
    assert salary_selection(output)["observed_score"] is None


def test_same_scope_salary_conflict_and_missing_units_prevent_selection():
    output = SalaryOutput(
        schema_version="1", state="present", records=[salary(), salary(minimum=120000)]
    )
    assert salary_selection(output)["state"] == "conflicting"
    output = SalaryOutput(
        schema_version="1", state="present", records=[salary(currency=None)]
    )
    assert salary_selection(output)["state"] == "ambiguous"


def test_missing_salary_stays_missing_not_zero():
    output = SalaryOutput(schema_version="1", state="not_stated", records=[])
    assert salary_selection(output) == {
        "state": "not_stated",
        "records": [],
        "observed_score": None,
    }


def test_invalid_numeric_types_and_units_are_rejected():
    with pytest.raises(ValidationError):
        fact(kind="experience", value="5", unit="years", scope="professional")
    with pytest.raises(ValidationError):
        salary(minimum=-1)


def test_unsupported_predicates_cannot_enter_decisions():
    with pytest.raises(ValidationError):
        fact(kind="seniority", value="senior", predicate="lt")
    with pytest.raises(ValidationError):
        fact(kind="enrollment", value="enrolled", predicate="gt")


def test_unquantified_experience_does_not_require_invented_time_unit():
    item = fact(
        kind="experience",
        value=None,
        predicate="exists",
        unit="none",
        scope="professional",
    )
    assert item.unit == "none"


def test_exact_degree_is_not_silently_a_minimum():
    output = qualification([fact(value="master", predicate="eq")])
    assert evaluate(output, [candidate(degree="phd")]).truth == Truth.UNKNOWN


def test_ambiguous_coverage_without_required_tree_keeps_warning():
    output = qualification([fact(kind="skill", value="Python", predicate="exists")])
    output.coverage["degree"] = "ambiguous"
    assert finalize(output, [candidate()])["review_required"]


def test_unavailable_coverage_is_incomplete_not_a_clean_apply():
    output = qualification([fact(kind="skill", value="Python", predicate="exists")])
    output.coverage["degree"] = "unavailable"
    assert finalize(output, [candidate()])["status"] == "Pending"


@pytest.mark.parametrize(
    "op,children,expected",
    [
        ("all_of", [Truth.TRUE, Truth.UNKNOWN], Truth.UNKNOWN),
        ("all_of", [Truth.FALSE, Truth.UNKNOWN], Truth.FALSE),
        ("any_of", [Truth.TRUE, Truth.UNKNOWN], Truth.TRUE),
        ("any_of", [Truth.FALSE, Truth.UNKNOWN], Truth.UNKNOWN),
    ],
)
def test_three_value_truth_table(op, children, expected):
    assert combine(op, children) == expected
