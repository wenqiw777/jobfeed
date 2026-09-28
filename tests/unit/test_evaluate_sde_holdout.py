from __future__ import annotations

from scripts.evaluate_sde_holdout import apply_adjudications, classification_metrics


def test_adjudications_replace_only_matching_holdout_decisions() -> None:
    rows = [
        {"job_id": "1", "is_sde_job": True},
        {"job_id": "2", "is_sde_job": False},
    ]

    merged = apply_adjudications(rows, {"2": True})

    assert [row["is_sde_job"] for row in merged] == [True, True]


def test_metrics_report_both_false_positive_and_false_negative_rates() -> None:
    metrics = classification_metrics(
        labels=[True, True, False, False], predictions=[True, False, True, False]
    )

    assert metrics == {
        "size": 4,
        "positive": 2,
        "negative": 2,
        "sde_recall": 0.5,
        "sde_precision": 0.5,
        "non_sde_recall": 0.5,
        "false_positive_rate": 0.5,
        "false_negative_rate": 0.5,
    }
