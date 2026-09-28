"""Tests for the local high-recall Block candidate model."""

import numpy as np

from scripts.demo_local_blocker_candidate import (
    choose_recall_threshold,
    chunk_text,
    document_scores,
)


def test_chunk_text_covers_full_document_with_overlap():
    text = "abcdefghijklmnopqrstuvwxyz"

    chunks = chunk_text(text, size=10, overlap=3)

    assert chunks == [
        "abcdefghij",
        "hijklmnopq",
        "opqrstuvwx",
        "vwxyz",
    ]


def test_document_scores_take_maximum_chunk_score():
    scores = np.asarray([0.1, 0.8, 0.3, 0.4])

    assert document_scores([1, 1, 2, 2], scores) == {1: 0.8, 2: 0.4}


def test_recall_threshold_uses_positive_document_scores():
    scores = {1: 0.9, 2: 0.2, 3: 0.7}
    labels = {1: 1, 2: 0, 3: 1}
    expected = min(score for key, score in scores.items() if labels[key] == 1)

    assert choose_recall_threshold(scores, labels, minimum_recall=1.0) == expected
