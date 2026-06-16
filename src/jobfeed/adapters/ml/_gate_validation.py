"""Validation and model-resolution helpers for the XGBoost ML gate.

Split from ``xgboost_gate.py`` to keep the main adapter module within the
300-line production file limit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jobfeed.adapters.ml._embedder import _canonical_model_name
from jobfeed.adapters.ml._vectorize import EMBEDDING_DIM

_META_EMBEDDING_MODEL_KEY = "embedding_model"
_META_EMBEDDING_DIM_KEY = "embedding_dim"
_META_THRESHOLD_KEY = "threshold"


def resolve_model_path(model_dir: Path, model_version: str | None) -> Path:
    """Locate the explicit or latest ``v*.json`` model file.

    Args:
        model_dir: Directory containing ``v*.json`` model files.
        model_version: Explicit version stem, or ``None`` for latest.

    Returns:
        Path to the resolved model file.

    Raises:
        FileNotFoundError: If no matching model file exists.
    """
    if model_version is not None:
        model_path = model_dir / f"{model_version}.json"
        if model_path.exists():
            return model_path
        raise FileNotFoundError(
            f"ML-gate model {model_version}.json not found in {model_dir}"
        )
    model_files = sorted(
        path
        for path in model_dir.glob("v*.json")
        if not path.name.endswith(".meta.json")
    )
    if not model_files:
        raise FileNotFoundError(f"No ML-gate model (v*.json) found in {model_dir}")
    return model_files[-1]


def read_meta(model_dir: Path, version: str) -> dict[str, Any]:
    """Read and validate required model metadata.

    Args:
        model_dir: Directory holding the ``v*.meta.json`` sidecar.
        version: Model version stem (e.g. ``"v20260101T000000Z"``).

    Returns:
        Parsed metadata dictionary.

    Raises:
        FileNotFoundError: If the meta sidecar is missing.
        ValueError: If required keys are absent.
    """
    meta_path = model_dir / f"{version}.meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"ML-gate model meta not found: {meta_path}")
    meta = cast(dict[str, Any], json.loads(meta_path.read_text()))
    missing = [
        key
        for key in (
            _META_THRESHOLD_KEY,
            _META_EMBEDDING_MODEL_KEY,
            _META_EMBEDDING_DIM_KEY,
        )
        if key not in meta
    ]
    if missing:
        raise ValueError(
            f"ML-gate model meta {meta_path.name} missing required keys: "
            f"{', '.join(missing)}"
        )
    return meta


def validate_embedding_contract(meta: dict[str, Any], model_name: str) -> None:
    """Fail fast when the configured embedder does not match model metadata.

    Args:
        meta: Parsed model metadata dictionary.
        model_name: Configured embedder model name.

    Raises:
        ValueError: If model name or embedding dimension mismatches.
    """
    expected_model = str(meta[_META_EMBEDDING_MODEL_KEY])
    actual_model = _canonical_model_name(model_name)
    if _canonical_model_name(expected_model) != actual_model:
        raise ValueError(
            "embedding_model mismatch: "
            f"model expects {expected_model!r}, configured {model_name!r}"
        )
    expected_dim = int(meta[_META_EMBEDDING_DIM_KEY])
    if expected_dim != EMBEDDING_DIM:
        raise ValueError(
            f"embedding_dim mismatch: model expects {expected_dim}, "
            f"vectorizer expects {EMBEDDING_DIM}"
        )
