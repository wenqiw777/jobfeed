"""Checks for the decomposed JD clause-classification experiment."""

from scripts.demo_jd_gliner2_clause_classifier import expand_records, score_labels
from scripts.demo_jd_gliner2_potential import EVAL_CASES


def test_each_eval_record_becomes_one_targeted_classification_example():
    records = expand_records(EVAL_CASES)

    assert len(records) == sum(
        len(items) for case in EVAL_CASES for items in case["gold"].values()
    )
    assert all(record["target"] in record["input"] for record in records)


def test_exact_label_score_requires_every_semantic_label():
    records = [
        {
            "case_id": "one",
            "target": "degree",
            "labels": {"kind": "degree", "modality": "required"},
        }
    ]

    score = score_labels(records, [{"kind": "degree", "modality": "preferred"}])

    assert score["exact_records"] == 0
    assert score["correct_labels"] == 1
    expected_labels = 2
    assert score["total_labels"] == expected_labels
