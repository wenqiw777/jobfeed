"""Tests for the end-to-end ModernBERT Block candidate experiment."""

from scripts.demo_modernbert_blocker_candidate import (
    max_document_logits,
    split_name,
)


def test_split_name_keeps_fixed_document_partitions():
    assert [split_name(sample_id) for sample_id in range(1, 11)] == [
        "development",
        "train",
        "train",
        "train",
        "test",
        "development",
        "train",
        "train",
        "train",
        "test",
    ]


def test_max_document_logits_preserves_local_conflict_signal():
    assert max_document_logits([[1.0, 7.0], [5.0, 3.0]]) == [5.0, 7.0]
