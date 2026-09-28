"""Checks for the 200-JD GLiNER2 evidence-candidate baseline."""

import pytest

from scripts.demo_jd_gliner2_200 import summarize_results, validate_entities


def test_validate_entities_requires_verbatim_in_bounds_spans():
    text = "Requires 2 years of experience."
    valid = {
        "entities": {
            "candidate years of experience requirement": [
                {"text": "2 years", "start": 9, "end": 16, "confidence": 0.9}
            ]
        }
    }

    assert validate_entities(text, valid) == valid["entities"]

    invalid = {
        "entities": {
            "candidate years of experience requirement": [
                {"text": "3 years", "start": 9, "end": 16, "confidence": 0.9}
            ]
        }
    }
    with pytest.raises(ValueError, match="not verbatim"):
        validate_entities(text, invalid)


def test_summarize_results_keeps_no_detection_distinct_from_absence():
    results = [
        {
            "sample_id": 1,
            "source_group": "official_unstructured",
            "entities": {"degree": [], "yoe": [{"text": "2 years"}]},
        },
        {
            "sample_id": 2,
            "source_group": "linkedin_only_in_corpus",
            "entities": {"degree": [{"text": "Bachelor's"}], "yoe": []},
        },
    ]

    summary = summarize_results(results, fields=("degree", "yoe"))

    assert summary["samples"] == len(results)
    assert summary["fields"]["degree"] == {
        "samples_with_candidates": 1,
        "samples_with_no_detection": 1,
        "candidate_count": 1,
    }
    assert summary["source_groups"]["official_unstructured"]["degree"] == {
        "samples_with_candidates": 0,
        "samples_with_no_detection": 1,
        "candidate_count": 0,
    }


def test_summarize_results_rejects_missing_field_outputs():
    with pytest.raises(ValueError, match="missing fields"):
        summarize_results(
            [
                {
                    "sample_id": 1,
                    "source_group": "official_unstructured",
                    "entities": {"degree": []},
                }
            ],
            fields=("degree", "yoe"),
        )
