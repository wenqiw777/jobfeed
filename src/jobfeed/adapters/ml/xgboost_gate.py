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

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from jobfeed.adapters.ml._embedder import (
    DEFAULT_MODEL_NAME,
    EmbedderProtocol,
    FastEmbedEmbedder,
)
from jobfeed.adapters.ml._gate_validation import (
    _META_THRESHOLD_KEY,
    read_meta,
    resolve_model_path,
    validate_embedding_contract,
)
from jobfeed.adapters.ml._vectorize import featurize
from jobfeed.domain.ml_features import (
    MLGateFeatures,
    extract_features,
    hard_fail_reason,
)
from jobfeed.domain.models import MLGateResult
from jobfeed.observability import get_tracer
from jobfeed.ports.ml_gate import GateInput

DEFAULT_MODEL_DIR = "models/ml_gate"
_BINARY_LOGISTIC = "binary:logistic"
FAIL_SCORE = 0.0
_tracer = get_tracer("jobfeed.ml_gate")


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
        meta = read_meta(Path(model_dir), version)
        validate_embedding_contract(meta, model_name)
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

        The CPU-bound work (feature extraction, embedding, booster) runs in
        a worker thread so the caller's event loop stays responsive; the
        async port contract is honored without the caller having to know
        this implementation is synchronous inside.

        Args:
            jobs: Gate inputs to score; ``result[i]`` corresponds to ``jobs[i]``.

        Returns:
            One ``MLGateResult`` per input, in the same order as ``jobs``.
        """
        if not jobs:
            return []
        return await asyncio.to_thread(self._predict_batch_sync, jobs)

    def _predict_batch_sync(self, jobs: list[GateInput]) -> list[MLGateResult]:
        with _tracer.start_as_current_span("extract_features"):
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
        with _tracer.start_as_current_span("format_input"):
            texts = [
                embedder.format_input(row.job.title, row.job.jd_text)
                for row in survivors
            ]
        with _tracer.start_as_current_span("embed"):
            embeddings = embedder.embed_batch(texts)
        with _tracer.start_as_current_span("featurize"):
            matrix = np.stack(
                [
                    featurize(row.features, embeddings[i])
                    for i, row in enumerate(survivors)
                ],
                axis=0,
            )
        with _tracer.start_as_current_span("predict"):
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

    model_path = resolve_model_path(model_dir, model_version)

    booster = xgb.Booster()
    booster.load_model(str(model_path))

    objective = json.loads(booster.save_config())["learner"]["objective"]["name"]
    if objective != _BINARY_LOGISTIC:
        raise ValueError(
            f"ML-gate model {model_path.name} has objective {objective!r}; "
            f"expected {_BINARY_LOGISTIC!r}"
        )
    return booster, model_path.stem


__all__ = ["EmbedderConfig", "XGBoostGate"]
