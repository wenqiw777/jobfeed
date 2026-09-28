"""Checks for the small GLiNER2 learnability experiment."""

import pytest

from scripts.demo_jd_gliner2_potential import (
    EVAL_CASES,
    TRAIN_CASES,
    evidence_values,
    score_predictions,
)


def test_train_and_eval_cases_are_distinct_and_evidence_is_verbatim():
    train_ids = {case["case_id"] for case in TRAIN_CASES}
    eval_ids = {case["case_id"] for case in EVAL_CASES}

    assert train_ids
    assert eval_ids
    assert train_ids.isdisjoint(eval_ids)
    assert {case["text"] for case in TRAIN_CASES}.isdisjoint(
        {case["text"] for case in EVAL_CASES}
    )

    for case in [*TRAIN_CASES, *EVAL_CASES]:
        for value in evidence_values(case["gold"]):
            assert value in case["text"]


def test_strict_score_penalizes_missing_extra_and_wrong_grouping():
    gold = {
        "qualification": [
            {
                "evidence": "Bachelor's degree and 3 years",
                "kind": "combined",
                "modality": "required",
                "subject": "candidate",
                "route": "A",
            },
            {
                "evidence": "Master's degree and 1 year",
                "kind": "combined",
                "modality": "required",
                "subject": "candidate",
                "route": "B",
            },
        ]
    }
    wrong = {
        "qualification": [
            {
                "evidence": "Bachelor's degree and 3 years",
                "kind": "combined",
                "modality": "required",
                "subject": "candidate",
                "route": "B",
            },
            {
                "evidence": "unexpected",
                "kind": "degree",
                "modality": "preferred",
                "subject": "candidate",
                "route": "standalone",
            },
        ]
    }

    metrics = score_predictions([{"gold": gold}], [wrong])

    assert metrics["exact_cases"] == 0
    assert metrics["total_cases"] == 1
    assert metrics["record_true_positives"] == 0
    expected_records = 2
    assert metrics["record_false_positives"] == expected_records
    assert metrics["record_false_negatives"] == expected_records


def test_score_rejects_prediction_count_mismatch():
    with pytest.raises(ValueError, match="prediction count"):
        score_predictions([{"gold": {}}], [])
