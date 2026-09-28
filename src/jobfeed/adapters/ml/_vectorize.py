"""Feature vectorizers for legacy ML gates and the full-JD SDE classifier.

``featurize(features, embedding)`` converts a structured ``MLGateFeatures``
record plus a 384-d sentence embedding into a single flat ``float32`` vector of
shape ``(450,)`` = ``[structured(66), embedding(384)]``, ready for XGBoost.

``featurize_sde_batch`` preserves that legacy block and appends a normalized
32,768-column sparse lexical block. Unlike the sentence embedding, this block
reads the complete JD and separately upweights title words and character
n-grams, so late responsibilities and precise occupation wording survive.

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
import re
import zlib
from itertools import pairwise

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp

from jobfeed.domain.ml_features import (
    DEGREE_LEVELS,
    DOMAIN_NAMES,
    ROLE_TYPES,
    SENIORITY_LEVELS,
    TECH_NAMES,
    MLGateFeatures,
)

EMBEDDING_DIM = 384
LEXICAL_HASH_DIM = 32_768
YOE_SCALE = 10.0
_TITLE_LIMIT = 180
_CHAR_NGRAMS = (3, 4, 5)
_WORD = re.compile(r"[a-z0-9+#.]{2,}")


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


def featurize_sde_batch(
    features: list[MLGateFeatures],
    embeddings: npt.NDArray[np.float32],
    titles: list[str],
    jd_texts: list[str],
) -> sp.csr_matrix:
    """Build SDE-v2 features from legacy signals plus title and the full JD.

    The legacy dense block remains first for backward conceptual parity. A
    normalized sparse lexical block follows it and covers every JD character;
    title character n-grams and title words receive extra weight so occupation
    wording is not diluted by long company boilerplate.

    Args:
        features: Legacy feature records in batch order.
        embeddings: Embedding rows aligned with the feature records.
        titles: Posting titles aligned with the feature records.
        jd_texts: Full job descriptions aligned with the feature records.

    Returns:
        CSR feature matrix with the legacy dense block followed by lexical features.

    Raises:
        ValueError: If input row counts differ or the batch is empty.
    """
    size = len(features)
    if len(titles) != size or len(jd_texts) != size or len(embeddings) != size:
        raise ValueError("SDE feature inputs must contain the same number of rows")
    dense = np.stack(
        [featurize(row, embeddings[index]) for index, row in enumerate(features)]
    )
    lexical = _lexical_matrix(titles, jd_texts)
    return sp.hstack([sp.csr_matrix(dense), lexical], format="csr").tocsr()


def _lexical_matrix(titles: list[str], jd_texts: list[str]) -> sp.csr_matrix:
    """Build normalized sparse lexical rows.

    Time complexity: O(C + T), where C is the total JD character count and T
    is the total bounded title length; n-gram widths and feature dimensions
    are fixed. Each accumulated nonzero column is normalized once.
    """
    row_indices: list[int] = []
    column_indices: list[int] = []
    values: list[float] = []
    for row_index, (raw_title, jd_text) in enumerate(
        zip(titles, jd_texts, strict=True)
    ):
        counts: dict[int, float] = {}
        title = " ".join(raw_title.casefold().split())[:_TITLE_LIMIT]
        padded_title = f"^{title}$"
        for width in _CHAR_NGRAMS:
            for start in range(len(padded_title) - width + 1):
                _add_hash(counts, f"tc:{padded_title[start : start + width]}", 2.0)
        title_words = _WORD.findall(title)
        _add_words(counts, "tw", "tb", title_words, weight=4.0)
        _add_words(
            counts,
            "jw",
            "jb",
            _WORD.findall(jd_text.casefold()),
            weight=1.0,
        )
        norm = sum(value * value for value in counts.values()) ** 0.5 or 1.0
        for column_index, value in counts.items():
            row_indices.append(row_index)
            column_indices.append(column_index)
            values.append(value / norm)
    return sp.csr_matrix(
        (values, (row_indices, column_indices)),
        shape=(len(titles), LEXICAL_HASH_DIM),
        dtype=np.float32,
    )


def _add_words(
    counts: dict[int, float],
    word_prefix: str,
    bigram_prefix: str,
    words: list[str],
    *,
    weight: float,
) -> None:
    for word in words:
        _add_hash(counts, f"{word_prefix}:{word}", weight)
    for first, second in pairwise(words):
        _add_hash(counts, f"{bigram_prefix}:{first} {second}", weight)


def _add_hash(counts: dict[int, float], token: str, weight: float) -> None:
    index = zlib.crc32(token.encode()) % LEXICAL_HASH_DIM
    counts[index] = counts.get(index, 0.0) + weight


__all__ = ["EMBEDDING_DIM", "LEXICAL_HASH_DIM", "featurize", "featurize_sde_batch"]
