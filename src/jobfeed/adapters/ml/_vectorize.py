"""Numpy feature vectorizer for the XGBoost ML gate (exact legacy numeric port).

``featurize(features, embedding)`` converts a structured ``MLGateFeatures``
record plus a 384-d sentence embedding into a single flat ``float32`` vector of
shape ``(450,)`` = ``[structured(66), embedding(384)]``, ready for XGBoost.

Layout (structured portion, length ``STRUCTURED_DIM`` = 66 — verbatim from the
legacy ``ml_gate.features.featurize``)::

    [0:5]    seniority_level one-hot   (SENIORITY_LEVELS; unknown -> all-zero)
    [5:9]    degree_required one-hot   (DEGREE_LEVELS; unknown -> all-zero)
    [9]      clearance_required        scalar 0/1
    [10]     school_restricted         scalar 0/1
    [11:16]  role_type one-hot         (ROLE_TYPES; unknown -> all-zero)
    [16]     yoe_min normalized        min(yoe/10, 1.0), 0.0 if None
    [17:26]  domain_tags binary        (DOMAIN_NAMES order)
    [26:65]  tech_required binary      (TECH_NAMES order, 39 wide)
    [65]     is_swe_role               scalar 0/1
    [66:450] sentence embedding        384-d, float32

The ordered vocab lists are imported from ``jobfeed.domain.ml_features`` (the
single source of truth) and are never redefined here.
"""

from __future__ import annotations

import contextlib

import numpy as np
import numpy.typing as npt

from jobfeed.domain.ml_features import (
    DEGREE_LEVELS,
    DOMAIN_NAMES,
    ROLE_TYPES,
    SENIORITY_LEVELS,
    TECH_NAMES,
    MLGateFeatures,
)

EMBEDDING_DIM = 384
YOE_SCALE = 10.0


def _one_hot(vocab: list[str], value: str) -> npt.NDArray[np.float32]:
    """One-hot ``value`` over ``vocab``; an unknown value yields all-zeros."""
    vec = np.zeros(len(vocab), dtype=np.float32)
    with contextlib.suppress(ValueError):  # unknown value -> all-zero (legacy)
        vec[vocab.index(value)] = 1.0
    return vec


def _binary_flags(vocab: list[str], present: list[str]) -> npt.NDArray[np.float32]:
    """1.0 at each ``vocab`` position whose name appears in ``present``."""
    present_set = set(present)
    return np.array(
        [1.0 if name in present_set else 0.0 for name in vocab],
        dtype=np.float32,
    )


def _yoe_norm(yoe_min: int | None) -> float:
    """Normalize years-of-experience to ``min(yoe/10, 1.0)``; ``None`` -> 0.0."""
    if yoe_min is None:
        return 0.0
    return min(float(yoe_min) / YOE_SCALE, 1.0)


def _structured(features: MLGateFeatures) -> npt.NDArray[np.float32]:
    """Concatenate the 66-d structured block in legacy layout order."""
    return np.concatenate(
        [
            _one_hot(SENIORITY_LEVELS, features.seniority_level),
            _one_hot(DEGREE_LEVELS, features.degree_required),
            np.array([float(features.clearance_required)], dtype=np.float32),
            np.array([float(features.school_restricted)], dtype=np.float32),
            _one_hot(ROLE_TYPES, features.role_type),
            np.array([_yoe_norm(features.yoe_min)], dtype=np.float32),
            _binary_flags(DOMAIN_NAMES, features.domain_tags),
            _binary_flags(TECH_NAMES, features.tech_required),
            np.array([1.0 if features.is_swe_role else 0.0], dtype=np.float32),
        ]
    )


def featurize(
    features: MLGateFeatures, embedding: npt.NDArray[np.float32]
) -> npt.NDArray[np.float32]:
    """Flatten structured features + a 384-d embedding into one float32 vector.

    Args:
        features: Structured rule-based features for one posting.
        embedding: The posting's sentence embedding; must be length 384.

    Returns:
        A ``float32`` array of shape ``(450,)`` laid out as documented above.

    Raises:
        ValueError: If ``embedding`` is not length 384.
    """
    emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if emb.shape[0] != EMBEDDING_DIM:
        raise ValueError(
            f"embedding must be length {EMBEDDING_DIM}, got {emb.shape[0]}"
        )
    return np.concatenate([_structured(features), emb])


__all__ = ["EMBEDDING_DIM", "featurize"]
