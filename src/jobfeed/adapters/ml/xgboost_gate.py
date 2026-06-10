"""XGBoost ML-gate adapter: extract -> hard-fail -> embed -> featurize -> predict.

``XGBoostGate`` implements the predict-only ``MLGate`` port over a committed
in-repo XGBoost model. Runtime wiring passes an explicit model version from
config; direct adapter use may omit it to load the latest ``v*.json`` in
``model_dir``. The required sibling ``v*.meta.json`` supplies the threshold and
embedding contract, then each batch runs the legacy predictor flow:

1. Extract structured features and check deterministic hard-fail rules. A
   hard-failed posting short-circuits to ``result="fail"`` with the rule reason
   and ``score=0.0`` — it is never embedded.
2. The surviving subset is formatted + embedded in ONE batch, vectorized to
   ``(m, 450)``, and scored by the booster. ``binary:logistic`` yields the
   positive-class probability directly; ``result="pass"`` iff ``score >=
   threshold`` (a model-driven fail has ``fail_reason=None``).
3. Results are re-aligned to the original input order (hard-failed rows
   interleaved), each carrying the model ``version`` and feature columns with
   ``int -> bool`` coercion for ``clearance_required`` / ``school_restricted``.

The ``xgboost`` import is lazy (deferred to load), so this module imports without
the toolchain present; only an actual load/predict needs it. The embedder is
injected (``FastEmbedEmbedder`` by default) and likewise lazy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt

from jobfeed.adapters.ml._embedder import (
    DEFAULT_MODEL_NAME,
    EmbedderProtocol,
    FastEmbedEmbedder,
    _canonical_model_name,
)
from jobfeed.adapters.ml._vectorize import EMBEDDING_DIM, featurize
from jobfeed.domain.ml_features import (
    MLGateFeatures,
    extract_features,
    hard_fail_reason,
)
from jobfeed.domain.models import MLGateResult
from jobfeed.ports.ml_gate import GateInput

DEFAULT_MODEL_DIR = "models/ml_gate"
_BINARY_LOGISTIC = "binary:logistic"
FAIL_SCORE = 0.0
_META_EMBEDDING_MODEL_KEY = "embedding_model"
_META_EMBEDDING_DIM_KEY = "embedding_dim"
_META_THRESHOLD_KEY = "threshold"


@dataclass(frozen=True, kw_only=True)
class EmbedderConfig:
    """Embedder knobs for XGBoostGate (max_chars, model_name).

    Bundled so ``XGBoostGate.__init__`` stays within the 5-argument limit.
    Ignored when an embedder is injected directly.
    """

    max_chars: int = 2000
    model_name: str | None = None


class XGBoostGate:
    """MLGate backed by a committed XGBoost ``binary:logistic`` booster."""

    def __init__(
        self,
        *,
        model_dir: str | Path = DEFAULT_MODEL_DIR,
        model_version: str | None = None,
        embedder: EmbedderProtocol | None = None,
        threshold_override: float | None = None,
        embedder_config: EmbedderConfig | None = None,
    ) -> None:
        """Load the selected model + metadata and prepare the embedder.

        Args:
            model_dir: Directory holding ``v*.json`` boosters and ``.meta.json``
                sidecars; the lexicographically-latest model is loaded.
            model_version: Explicit model version stem to load. ``None`` keeps
                the latest-version behavior for direct adapter tests.
            embedder: Injected text embedder; defaults to a lazily-loaded
                ``FastEmbedEmbedder``.
            threshold_override: When set, overrides the meta-file threshold.
            embedder_config: Embedder knobs; uses defaults when omitted.
                Ignored when ``embedder`` is injected.

        Raises:
            FileNotFoundError: If ``model_dir`` holds no ``v*.json`` model.
            ValueError: If the loaded booster's objective is not binary:logistic.
        """
        cfg = embedder_config or EmbedderConfig()
        self._embedding_max_chars = cfg.max_chars
        model_name = cfg.model_name or DEFAULT_MODEL_NAME
        self._embedding_model = model_name
        self._embedder = embedder
        booster, version = _load_booster(Path(model_dir), model_version=model_version)
        self._booster: Any = booster
        self._version = version
        meta = _read_meta(Path(model_dir), version)
        _validate_embedding_contract(meta, model_name)
        meta_threshold = float(meta[_META_THRESHOLD_KEY])
        self._threshold = (
            threshold_override if threshold_override is not None else meta_threshold
        )

    @property
    def _active_embedder(self) -> EmbedderProtocol:
        """Lazily construct the default embedder on first use.

        When no embedder was injected, build the default ``FastEmbedEmbedder``
        with the configured ``embedding_model`` (falling back to its built-in
        default when unset). Construction stays toolchain-free — the model loads
        only on first ``embed_batch``.
        """
        if self._embedder is None:
            self._embedder = FastEmbedEmbedder(
                model_name=self._embedding_model or DEFAULT_MODEL_NAME,
                max_chars=self._embedding_max_chars,
            )
        return self._embedder

    async def predict_batch(self, jobs: list[GateInput]) -> list[MLGateResult]:
        """Score a batch of jobs, one ordered result per input.

        Args:
            jobs: Gate inputs to score; ``result[i]`` corresponds to ``jobs[i]``.

        Returns:
            One ``MLGateResult`` per input, in the same order as ``jobs``.
        """
        if not jobs:
            return []
        rows = [
            _RowState(extract_features(job.title, job.jd_text), job) for job in jobs
        ]
        survivors = [row for row in rows if row.hard_fail is None]
        self._score_survivors(survivors)
        return [row.to_result(self._version) for row in rows]

    def _score_survivors(self, survivors: list[_RowState]) -> None:
        """Embed + featurize + score the non-hard-failed rows in one batch."""
        if not survivors:
            return
        embedder = self._active_embedder
        texts = [
            embedder.format_input(row.job.title, row.job.jd_text) for row in survivors
        ]
        embeddings = embedder.embed_batch(texts)
        matrix = np.stack(
            [featurize(row.features, embeddings[i]) for i, row in enumerate(survivors)],
            axis=0,
        )
        scores = self._predict(matrix)
        for row, score in zip(survivors, scores, strict=True):
            row.apply_model_score(float(score), self._threshold)

    def _predict(self, matrix: npt.NDArray[np.float32]) -> npt.NDArray[np.float64]:
        """Run the booster; binary:logistic returns positive-class probability."""
        import xgboost as xgb  # noqa: PLC0415

        scores = self._booster.predict(xgb.DMatrix(matrix), output_margin=False)
        return np.asarray(scores, dtype=np.float64)


class _RowState:
    """Mutable per-input scratch: features, hard-fail verdict, model verdict."""

    def __init__(self, features: MLGateFeatures, job: GateInput) -> None:
        self.features = features
        self.job = job
        self.hard_fail = hard_fail_reason(features)
        self.result = "fail"
        self.fail_reason: str | None = self.hard_fail
        self.score = FAIL_SCORE

    def apply_model_score(self, score: float, threshold: float) -> None:
        """Record the model verdict for a non-hard-failed row."""
        self.score = score
        if score >= threshold:
            self.result = "pass"
            self.fail_reason = None
        else:
            self.result = "fail"
            self.fail_reason = None  # model-driven fail carries no reason string

    def to_result(self, version: str) -> MLGateResult:
        """Build the ordered ``MLGateResult``, coercing int columns to bool."""
        features = self.features
        return MLGateResult(
            score=self.score,
            result=self.result,
            fail_reason=self.fail_reason,
            version=version,
            is_swe_role=features.is_swe_role,
            seniority_level=features.seniority_level,
            degree_required=features.degree_required,
            clearance_required=bool(features.clearance_required),
            school_restricted=bool(features.school_restricted),
            yoe_min=features.yoe_min,
            domain_tags=features.domain_tags,
            tech_required=features.tech_required,
            role_type=features.role_type,
        )


def _load_booster(model_dir: Path, *, model_version: str | None) -> tuple[Any, str]:
    """Load an explicit/latest ``v*.json`` booster; validate the objective.

    Returns:
        The loaded booster and its version (model-file stem).

    Raises:
        FileNotFoundError: If no ``v*.json`` model exists in ``model_dir``.
        ValueError: If the booster objective is not ``binary:logistic``.
    """
    # The embedder runs on onnxruntime (no torch), so xgboost is the only OpenMP
    # runtime in-process — no dual-load collision, no OMP pin needed here.
    import xgboost as xgb  # noqa: PLC0415

    model_path = _resolve_model_path(model_dir, model_version)

    booster = xgb.Booster()
    booster.load_model(str(model_path))

    objective = json.loads(booster.save_config())["learner"]["objective"]["name"]
    if objective != _BINARY_LOGISTIC:
        raise ValueError(
            f"ML-gate model {model_path.name} has objective {objective!r}; "
            f"expected {_BINARY_LOGISTIC!r}"
        )
    return booster, model_path.stem


def _resolve_model_path(model_dir: Path, model_version: str | None) -> Path:
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


def _read_meta(model_dir: Path, version: str) -> dict[str, Any]:
    """Read and validate required model metadata."""
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


def _validate_embedding_contract(meta: dict[str, Any], model_name: str) -> None:
    """Fail fast when configured embedder does not match model metadata."""
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


__all__ = ["EmbedderConfig", "XGBoostGate"]
