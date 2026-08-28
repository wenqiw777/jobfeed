"""Pure training-policy helpers for the independent seniority classifier."""

from __future__ import annotations

import math


def choose_recall_threshold(
    labels: list[int],
    scores: list[float],
    *,
    minimum_in_scope_recall: float = 0.99,
) -> float:
    """Choose the most useful threshold satisfying in-scope recall.

    Args:
        labels: Binary labels where one means out-of-scope.
        scores: Ordered out-of-scope probabilities.
        minimum_in_scope_recall: Required recall for in-scope examples.

    Returns:
        Lowest useful threshold at the best valid blocking recall.

    Raises:
        ValueError: If inputs are empty, misaligned, invalid, or lack negatives.
    """
    if len(labels) != len(scores) or not labels:
        raise ValueError("seniority labels and scores must be non-empty and aligned")
    if not 0.0 <= minimum_in_scope_recall <= 1.0:
        raise ValueError("minimum recall must be in [0, 1]")
    if any(label not in (0, 1) for label in labels):
        raise ValueError("seniority labels must be binary")
    in_scope_total = sum(label == 0 for label in labels)
    if in_scope_total == 0:
        raise ValueError("validation data must contain in-scope examples")

    candidates = sorted(set(scores))
    candidates.append(math.nextafter(max(scores), math.inf))
    valid: list[tuple[int, float]] = []
    for threshold in candidates:
        false_blocks = sum(
            label == 0 and score >= threshold
            for label, score in zip(labels, scores, strict=True)
        )
        recall = 1.0 - false_blocks / in_scope_total
        if recall < minimum_in_scope_recall:
            continue
        true_blocks = sum(
            label == 1 and score >= threshold
            for label, score in zip(labels, scores, strict=True)
        )
        valid.append((true_blocks, threshold))
    if not valid:
        return math.nextafter(max(scores), math.inf)
    return max(valid, key=lambda item: (item[0], -item[1]))[1]


__all__ = ["choose_recall_threshold"]
