"""Boundary tests for the offline demo; no network/model calls."""

import asyncio
import json
from argparse import Namespace

from scripts.demo_jd_contract import (
    contract_demo,
    extraction_schema,
    parse_and_validate,
    prompt_for,
    run_pilot,
)
from scripts.jd_semantic_contract import Document, SalaryOutput


def test_prompt_retains_full_text_and_does_not_use_a_sentence_selector():
    text = (
        "Company background\n" + "Long responsibility. " * 500 + "\nNo salary listed."
    )
    doc = Document(
        job_id="1",
        title="Role",
        url="https://example.test/1",
        text=text,
        completeness="complete",
    )
    prompt = prompt_for(doc, "salary")
    payload = json.loads(prompt.split("DOCUMENT_JSON:\n", 1)[1])
    assert payload["text"] == text


def test_schema_has_all_coverage_fields_and_both_relationship_trees():
    schema = extraction_schema("qualification")
    properties = schema["properties"]
    assert {"required", "preferred", "facts", "coverage"} <= set(properties)
    assert properties["coverage"]["additionalProperties"] is False
    assert "experience" in properties["coverage"]["required"]


def test_invalid_and_ungrounded_salary_never_looks_valid():
    doc = Document(
        job_id="1",
        title="Role",
        url="https://example.test/1",
        text="No salary.",
        completeness="complete",
    )
    parsed, issues = parse_and_validate({"wrong": 1}, "salary", doc)
    assert parsed is None and issues
    raw = {
        "schema_version": "1",
        "state": "present",
        "records": [
            {
                "minimum": 100000,
                "maximum": 200000,
                "currency": "USD",
                "interval": "year",
                "kind": "base",
                "location": "US",
                "level": "unspecified",
                "state": "present",
                "evidence": [{"quote": "USD 100000-200000", "block_id": "body"}],
            }
        ],
    }
    parsed, issues = parse_and_validate(raw, "salary", doc)
    assert isinstance(parsed, SalaryOutput)
    assert issues == ["evidence_not_in_document"]


def test_contract_demo_labels_fixtures_not_live_model_accuracy(capsys):
    contract_demo()
    output = json.loads(capsys.readouterr().out)
    assert output["fixture_type"] == "constructed_semantic_inputs_not_model_extractions"
    assert all(row["status"] == row["expected"] for row in output["cases"])


def test_salary_schema_requires_attribute_evidence():
    schema = extraction_schema("salary")
    assert schema["properties"]["schema_version"]["const"] == "2"
    assert "attributes" in schema["$defs"]["Salary"]["required"]


def test_valid_tasks_do_not_call_a_review_model(tmp_path, monkeypatch):
    _run_routing_case(tmp_path, monkeypatch, invalid_first=False)


def test_invalid_task_gets_one_repair_without_accepting_a_second_failure(
    tmp_path, monkeypatch
):
    _run_routing_case(tmp_path, monkeypatch, invalid_first=True)


def _run_routing_case(tmp_path, monkeypatch, invalid_first):
    doc = Document(
        job_id="1",
        title="Role",
        url="https://example.test/1",
        text="Build tools.",
        completeness="complete",
    )
    calls = []

    async def fake_call(_document, task, model, **kwargs):
        calls.append((task, model, kwargs.get("feedback")))
        if invalid_first and task == "salary":
            return {
                "state": "invalid",
                "validation_errors": ["unsupported"],
                "output": {},
            }
        output = {"schema_version": "2", "state": "not_stated"}
        if task == "salary":
            output["records"] = []
        else:
            output.update(
                facts=[],
                coverage=dict.fromkeys(
                    extraction_schema("qualification")["properties"]["coverage"][
                        "required"
                    ],
                    "not_stated",
                ),
                required={"op": "all_of", "ref": None, "children": []},
                preferred={"op": "all_of", "ref": None, "children": []},
            )
        return {"state": "valid", "output": output}

    monkeypatch.setattr(
        "scripts.demo_jd_contract.load_documents", lambda *_args: [(1, doc)]
    )
    monkeypatch.setattr("scripts.demo_jd_contract.call_model", fake_call)
    args = Namespace(
        output=tmp_path / "pilot",
        inputs=None,
        manifest=None,
        database=None,
        ids=[1],
        concurrency=1,
        model="first",
        review_model="repair",
        timeout=1,
        review_policy="on_error",
    )
    asyncio.run(run_pilot(args))
    summary = json.loads((args.output / "summary.json").read_text())
    assert len(calls) == (3 if invalid_first else 2)
    assert summary["accepted_tasks"] == (1 if invalid_first else 2)
    if invalid_first:
        assert calls[-1] == ("salary", "repair", ["unsupported"])
        assert (
            json.loads((args.output / "1-salary.json").read_text())["accepted"] is None
        )
