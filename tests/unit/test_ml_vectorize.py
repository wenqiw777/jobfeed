"""Unit tests for the numpy ML-gate vectorizer (``adapters.ml._vectorize``).

Pure + fast: runs under ``make quality`` with numpy and a FAKE 384-vector,
never loading fastembed or xgboost. Pins the exact 450-dim layout
(structured 66 + embedding 384) against the domain vocab order so a future
vocab edit that desyncs the numeric port fails loudly here.
"""

from __future__ import annotations

import numpy as np
import pytest

from jobfeed.adapters.ml._vectorize import featurize
from jobfeed.domain.ml_features import (
    DEGREE_LEVELS,
    DOMAIN_NAMES,
    ROLE_TYPES,
    SENIORITY_LEVELS,
    STRUCTURED_DIM,
    TECH_NAMES,
    MLGateFeatures,
)

EMBED_DIM = 384
TOTAL_DIM = 450


def _fake_embedding(fill: float = 0.5) -> np.ndarray:
    """A deterministic, length-384 stand-in for a real sentence embedding."""
    return np.full(EMBED_DIM, fill, dtype=np.float32)


def _features(**overrides: object) -> MLGateFeatures:
    """Build MLGateFeatures with clean defaults, overriding select fields."""
    base: dict[str, object] = {
        "seniority_level": "unknown",
        "degree_required": "none",
        "clearance_required": 0,
        "clearance_status": "none",
        "school_restricted": 0,
        "domain_tags": [],
        "tech_required": [],
        "role_type": "fte",
        "yoe_min": None,
        "is_swe_role": True,
    }
    base.update(overrides)
    return MLGateFeatures(**base)  # type: ignore[arg-type]


def test_featurize_returns_float32_shape_450() -> None:
    """The flat vector is float32 with shape (450,) = structured 66 + embed 384."""
    vec = featurize(_features(), _fake_embedding())
    assert vec.dtype == np.float32
    assert vec.shape == (TOTAL_DIM,)
    assert STRUCTURED_DIM == 66
    assert STRUCTURED_DIM + EMBED_DIM == TOTAL_DIM


def test_seniority_one_hot_position_matches_vocab() -> None:
    """[0:5] one-hot over SENIORITY_LEVELS at the vocab index."""
    for idx, level in enumerate(SENIORITY_LEVELS):
        vec = featurize(_features(seniority_level=level), _fake_embedding())
        block = vec[0:5]
        assert block[idx] == 1.0
        assert block.sum() == 1.0


def test_degree_one_hot_position_matches_vocab() -> None:
    """[5:9] one-hot over DEGREE_LEVELS at the vocab index."""
    for idx, level in enumerate(DEGREE_LEVELS):
        vec = featurize(_features(degree_required=level), _fake_embedding())
        block = vec[5:9]
        assert block[idx] == 1.0
        assert block.sum() == 1.0


def test_clearance_and_school_scalars() -> None:
    """[9] clearance_required and [10] school_restricted as 0/1 floats."""
    vec = featurize(
        _features(clearance_required=1, school_restricted=1), _fake_embedding()
    )
    assert vec[9] == 1.0
    assert vec[10] == 1.0
    vec0 = featurize(
        _features(clearance_required=0, school_restricted=0), _fake_embedding()
    )
    assert vec0[9] == 0.0
    assert vec0[10] == 0.0


def test_role_one_hot_position_matches_vocab() -> None:
    """[11:16] one-hot over ROLE_TYPES at the vocab index."""
    for idx, role in enumerate(ROLE_TYPES):
        vec = featurize(_features(role_type=role), _fake_embedding())
        block = vec[11:16]
        assert block[idx] == 1.0
        assert block.sum() == 1.0


def test_yoe_norm_none_is_zero() -> None:
    """yoe_min=None -> [16] == 0.0."""
    vec = featurize(_features(yoe_min=None), _fake_embedding())
    assert vec[16] == 0.0


def test_yoe_norm_capped_at_one() -> None:
    """yoe_min=25 -> [16] == 1.0 (min(25/10, 1.0))."""
    vec = featurize(_features(yoe_min=25), _fake_embedding())
    assert vec[16] == 1.0


def test_yoe_norm_scales_below_cap() -> None:
    """yoe_min below 10 scales linearly: yoe_min=3 -> [16] == 0.3."""
    vec = featurize(_features(yoe_min=3), _fake_embedding())
    assert vec[16] == pytest.approx(0.3)


def test_unknown_seniority_one_hot_all_zeros() -> None:
    """An out-of-vocab seniority -> the whole [0:5] block is zero."""
    vec = featurize(_features(seniority_level="staff++"), _fake_embedding())
    assert vec[0:5].sum() == 0.0


def test_unknown_degree_one_hot_all_zeros() -> None:
    """An out-of-vocab degree -> the whole [5:9] block is zero."""
    vec = featurize(_features(degree_required="associate"), _fake_embedding())
    assert vec[5:9].sum() == 0.0


def test_unknown_role_one_hot_all_zeros() -> None:
    """An out-of-vocab role -> the whole [11:16] block is zero."""
    vec = featurize(_features(role_type="seasonal"), _fake_embedding())
    assert vec[11:16].sum() == 0.0


def test_domain_binary_flags_match_vocab_positions() -> None:
    """[17:26] binary flags set at exactly the DOMAIN_NAMES indices present."""
    present = [DOMAIN_NAMES[0], DOMAIN_NAMES[3]]
    vec = featurize(_features(domain_tags=present), _fake_embedding())
    block = vec[17:26]
    expected = np.array(
        [1.0 if name in set(present) else 0.0 for name in DOMAIN_NAMES],
        dtype=np.float32,
    )
    assert np.array_equal(block, expected)
    assert block[0] == 1.0
    assert block[3] == 1.0
    assert block.sum() == 2.0


def test_tech_binary_flags_match_vocab_positions() -> None:
    """[26:65] binary flags set at exactly the TECH_NAMES indices present."""
    present = [TECH_NAMES[0], TECH_NAMES[4], TECH_NAMES[-1]]
    vec = featurize(_features(tech_required=present), _fake_embedding())
    block = vec[26:65]
    expected = np.array(
        [1.0 if name in set(present) else 0.0 for name in TECH_NAMES],
        dtype=np.float32,
    )
    assert np.array_equal(block, expected)
    assert block.sum() == 3.0


def test_is_swe_role_flag_at_index_65() -> None:
    """[65] == 1.0 when is_swe_role else 0.0."""
    assert featurize(_features(is_swe_role=True), _fake_embedding())[65] == 1.0
    assert featurize(_features(is_swe_role=False), _fake_embedding())[65] == 0.0


def test_embedding_placed_at_tail() -> None:
    """[66:450] holds the 384-d embedding verbatim (as float32)."""
    emb = np.linspace(-1.0, 1.0, EMBED_DIM).astype(np.float64)
    vec = featurize(_features(), emb)
    assert np.allclose(vec[66:450], emb.astype(np.float32))


def test_rejects_wrong_length_embedding() -> None:
    """An embedding that is not length 384 raises ValueError."""
    with pytest.raises(ValueError):
        featurize(_features(), np.zeros(383, dtype=np.float32))
    with pytest.raises(ValueError):
        featurize(_features(), np.zeros(385, dtype=np.float32))
