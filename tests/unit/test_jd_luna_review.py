"""Selective semantic review must not turn uncertainty into confirmed facts."""

import asyncio
import copy
import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.demo_jd_contract import call_model
from scripts.demo_jd_luna_review import review_task, run
from scripts.jd_luna_review import review_prompt, review_reasons, validate_review
from scripts.jd_semantic_contract import (
    Document,
    Evidence,
    Salary,
    SalaryAttributes,
    SalaryOutput,
)
from tests.unit.test_jd_semantic_contract import fact, group, leaf, qualification


def document():
    return Document(
        job_id="fixture",
        title="Engineer",
        url="https://example.test/job",
        text="Requirement. Build tools. Equity may be offered.",
        completeness="complete",
    )


def absent_salary():
    return {"schema_version": "2", "state": "not_stated", "records": []}


def result(output):
    return {"state": "valid", "output": output}


def test_absence_alone_does_not_call_luna_but_audit_and_block_do():
    primary = result(absent_salary())
    assert review_reasons(primary, "salary", document()) == []
    assert "audit" in review_reasons(primary, "salary", document(), audit=True)
    assert "proposed_block" in review_reasons(
        primary, "salary", document(), proposed_block=True
    )


def test_conflict_and_complex_alternatives_route_without_a_schema_error():
    q = qualification(
        [fact("b", value="bachelor"), fact("m")], group("any_of", leaf("b"), leaf("m"))
    )
    assert "alternatives" in review_reasons(
        result(q.model_dump()), "qualification", document()
    )
    q.coverage["graduation"] = "conflicting"
    assert "uncertainty" in review_reasons(
        result(q.model_dump()), "qualification", document()
    )


def test_upper_experience_bound_and_waiver_route():
    for f, reason in [
        (
            fact(kind="experience", value=7, unit="years", predicate="lte"),
            "experience_upper_bound",
        ),
        (fact(value=None, predicate="waived", modality="waived"), "waiver"),
    ]:
        q = qualification([f])
        assert reason in review_reasons(
            result(q.model_dump()), "qualification", document()
        )


def test_repair_removal_is_not_missed_when_latest_output_is_valid():
    before = result(qualification([fact()]).model_dump())
    after = result(qualification([], state="not_stated").model_dump())
    assert "prior_items_changed" in review_reasons(
        after, "qualification", document(), previous=before
    )


def envelope():
    return {
        "decision": "resolved",
        "answers": [
            {
                "reason": "audit",
                "decision": "resolved",
                "explanation": "No salary stated.",
                "evidence": [],
            }
        ],
        "dispositions": [],
        "output": absent_salary(),
    }


def test_valid_review_is_not_an_accuracy_claim():
    reviewed, errors = validate_review(
        envelope(), "salary", document(), ["audit"], absent_salary()
    )
    assert reviewed is not None and errors == []


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("missing_answer", "review.reason_coverage"),
        ("invented_quote", "review.evidence_not_in_document"),
        ("contradictory_decision", "review.unresolved_answer"),
    ],
)
def test_review_needs_issue_coverage_and_grounded_explanations(mutation, expected):
    raw = envelope()
    if mutation == "missing_answer":
        raw["answers"] = []
    elif mutation == "invented_quote":
        raw["answers"][0]["evidence"] = [
            Evidence(quote="USD 90000", block_id="body").model_dump()
        ]
    else:
        raw["answers"][0]["decision"] = "uncertain"
    _, errors = validate_review(raw, "salary", document(), ["audit"], absent_salary())
    assert expected in errors


def test_review_cannot_silently_drop_prior_fact():
    prior = qualification([fact()]).model_dump()
    raw = envelope()
    raw["output"] = qualification([], state="not_stated").model_dump()
    _, errors = validate_review(raw, "qualification", document(), ["audit"], prior)
    assert "review.prior_item_coverage" in errors


def test_uncertain_review_remains_uncertain():
    raw = copy.deepcopy(envelope())
    raw["decision"] = "uncertain"
    raw["answers"][0]["decision"] = "uncertain"
    reviewed, errors = validate_review(
        raw, "salary", document(), ["audit"], absent_salary()
    )
    assert errors == [] and reviewed.decision == "uncertain"


@pytest.mark.parametrize(
    "state,decision,expected",
    [
        ("valid", "resolved", "reviewed"),
        ("valid", "uncertain", "review_required"),
        ("invalid", "resolved", "review_required"),
        ("pending", None, "review_required"),
    ],
)
def test_one_review_only_and_no_silent_primary_fallback(state, decision, expected):
    calls = []
    primary = result(absent_salary())
    revised = result(absent_salary()) | {"state": state, "review_decision": decision}

    async def fake(doc, task, model, **kwargs):
        calls.append((doc, task, model, kwargs))
        return revised

    row = asyncio.run(
        review_task(document(), "salary", primary, audit=True, caller=fake)
    )
    assert len(calls) == 1
    assert calls[0][:3] == (document(), "salary", "gpt-5.6-luna")
    assert calls[0][3]["review_context"]["prior_output"] == primary["output"]
    assert row["resolution_state"] == expected
    assert row["accepted"] == (revised if expected == "reviewed" else None)
    assert row["observed_score"] is None and row["production_verdict"] is None


def test_missing_salary_does_not_call_model():
    async def forbidden(*_args, **_kwargs):
        pytest.fail("Absent salary must not trigger another model")

    primary = result(absent_salary())
    row = asyncio.run(review_task(document(), "salary", primary, caller=forbidden))
    assert row["accepted"] == primary
    assert row["resolution_state"] == "not_reviewed"


def test_unclear_salary_component_and_period_are_reviewed():
    salary = Salary(
        minimum=None,
        maximum=None,
        currency=None,
        interval="unknown",
        kind="unspecified",
        location="unspecified",
        level="unspecified",
        state="present",
        evidence=[Evidence(quote="Requirement", block_id="body")],
        attributes=SalaryAttributes(
            **{
                key: []
                for key in (
                    "minimum",
                    "maximum",
                    "currency",
                    "interval",
                    "kind",
                    "location",
                    "level",
                )
            }
        ),
    )
    raw = SalaryOutput(
        schema_version="2", state="present", records=[salary]
    ).model_dump()
    assert "compensation_unknown_attributes" in review_reasons(
        result(raw), "salary", document()
    )


def test_incomplete_source_cannot_be_confirmed_by_reviewer():
    doc = document().model_copy(update={"completeness": "partial"})
    _, errors = validate_review(envelope(), "salary", doc, ["audit"], absent_salary())
    assert "review.incomplete_source" in errors


def test_review_deletion_requires_evidence_and_preserved_cannot_change_value():
    prior = qualification([fact()]).model_dump()
    raw = envelope()
    raw["output"] = qualification([fact(value="bachelor")]).model_dump()
    raw["dispositions"] = [
        {
            "source_index": 0,
            "action": "preserved",
            "output_indices": [0],
            "explanation": "Same fact",
            "evidence": [],
        }
    ]
    _, errors = validate_review(raw, "qualification", document(), ["audit"], prior)
    assert "review.preserved_item_changed" in errors
    raw["output"] = qualification([], state="not_stated").model_dump()
    raw["dispositions"][0].update(action="removed", output_indices=[])
    _, errors = validate_review(raw, "qualification", document(), ["audit"], prior)
    assert "review.change_requires_evidence" in errors


def test_proposed_block_is_reviewed_but_never_becomes_a_production_verdict():
    calls = []

    async def fake(*_args, **kwargs):
        calls.append(kwargs["review_context"])
        return result(absent_salary()) | {"review_decision": "resolved"}

    row = asyncio.run(
        review_task(
            document(),
            "salary",
            result(absent_salary()),
            proposed_block=True,
            caller=fake,
        )
    )
    assert calls[0]["reasons"] == ["proposed_block"]
    assert row["production_verdict"] is None


def test_real_transport_builder_sends_full_source_prior_and_review_schema(monkeypatch):
    captured = {}

    async def spawn(*args, **_kwargs):
        captured["schema"] = json.loads(
            Path(args[args.index("--output-schema") + 1]).read_text()
        )
        output_path = Path(args[args.index("--output-last-message") + 1])

        class Process:
            returncode = 0

            async def communicate(self, prompt):
                captured["prompt"] = prompt.decode()
                output_path.write_text(json.dumps(envelope()))
                return b"", b""

        return Process()

    monkeypatch.setattr(
        "scripts.demo_jd_contract.asyncio.create_subprocess_exec", spawn
    )
    context = {"reasons": ["audit"], "prior_output": absent_salary()}
    row = asyncio.run(
        call_model(document(), "salary", "gpt-5.6-luna", review_context=context)
    )
    assert row["state"] == "valid" and row["review_decision"] == "resolved"
    assert row["output"] == absent_salary()
    prompt = captured["prompt"]
    source = prompt.split("DOCUMENT_JSON:\n", 1)[1].split("\nYou are the second", 1)[0]
    assert json.loads(source) == document().model_dump()
    assert json.loads(prompt.split("REVIEW_CONTEXT_JSON:\n", 1)[1]) == context
    schema = captured["schema"]
    assert schema["additionalProperties"] is False
    assert {"decision", "answers", "dispositions", "output"} == set(schema["required"])
    assert (
        schema["properties"]["output"]["properties"]["schema_version"]["const"] == "2"
    )


def test_review_prompts_keep_two_tasks_separate():
    q = review_prompt("qualification")
    s = review_prompt("salary")
    assert "QUALIFICATION ONLY" in q
    assert "Salary, benefits and authorization are outside this task" in q
    assert "COMPENSATION ONLY" in s
    assert "Do NOT review education or job qualifications" in s
    assert "DOES support numeric" in s
    assert "Referral rewards require an additional referral action" in s


def test_timeout_terminates_only_the_owned_process_group(monkeypatch):
    calls = []

    class Process:
        pid = 12345
        returncode = None

        async def communicate(self, data=None):
            if data is not None:
                raise TimeoutError
            return b"", b""

    async def spawn(*_args, **kwargs):
        assert kwargs["start_new_session"] is True
        return Process()

    monkeypatch.setattr(
        "scripts.demo_jd_contract.asyncio.create_subprocess_exec", spawn
    )
    monkeypatch.setattr(
        "scripts.demo_jd_contract.os.killpg", lambda pid, sig: calls.append((pid, sig))
    )
    row = asyncio.run(call_model(document(), "salary", "gpt-5.6-luna"))
    assert row["state"] == "pending" and row["error"] == "timeout"
    assert len(calls) == 1 and calls[0][0] == Process.pid


def test_fresh_demo_calls_primary_then_luna_and_persists_both(tmp_path, monkeypatch):
    inputs = tmp_path / "input.json"
    inputs.write_text(
        json.dumps([{"sample_id": 1, "document": document().model_dump()}])
    )
    calls = []

    async def fake(doc, task, model, **kwargs):
        calls.append((doc, task, model, kwargs))
        return result(absent_salary()) | {"review_decision": "resolved"}

    monkeypatch.setattr("scripts.demo_jd_luna_review.call_model", fake)
    args = Namespace(
        inputs=inputs,
        baseline=None,
        output=tmp_path / "out",
        ids=None,
        audit_ids=[1],
        tasks=["salary"],
        concurrency=1,
        model="gpt-5.6-sol",
        review_model="gpt-5.6-luna",
        timeout=1,
    )
    asyncio.run(run(args))
    assert [c[2] for c in calls] == ["gpt-5.6-sol", "gpt-5.6-luna"]
    row = json.loads((args.output / "1-salary.json").read_text())
    assert row["first"]["output"] == row["semantic_review"]["output"] == absent_salary()
    assert row["resolution_state"] == "reviewed"
    summary = json.loads((args.output / "summary.json").read_text())
    assert summary["primary_calls"] == summary["review_calls"] == 1
    assert summary["human_accuracy"] is None
