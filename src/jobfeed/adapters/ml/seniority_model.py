"""Independent XGBoost probability model for ambiguous seniority roles."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np

from jobfeed.adapters.ml._embedder import (
    DEFAULT_MODEL_NAME,
    EmbedderProtocol,
    FastEmbedEmbedder,
)
from jobfeed.adapters.ml._gate_validation import (
    read_meta,
    resolve_model_path,
    validate_embedding_contract,
)
from jobfeed.adapters.ml._vectorize import featurize
from jobfeed.domain.ml_features import extract_features
from jobfeed.domain.seniority import SeniorityInput

_BINARY_LOGISTIC = "binary:logistic"


class XGBoostSeniorityModel:
    """Predict out-of-scope probability without making the gate decision."""

    def __init__(
        self,
        *,
        model_dir: str | Path,
        model_version: str | None = None,
        embedder: EmbedderProtocol | None = None,
        embedding_model: str = DEFAULT_MODEL_NAME,
        embedding_max_chars: int = 2000,
    ) -> None:
        self._model_dir = Path(model_dir)
        model_path = resolve_model_path(self._model_dir, model_version)
        self._version = model_path.stem
        meta = read_meta(self._model_dir, self._version)
        validate_embedding_contract(meta, embedding_model)
        self._embedder = embedder or FastEmbedEmbedder(
            model_name=embedding_model,
            max_chars=embedding_max_chars,
        )
        self._booster = _load_booster(model_path)

    @property
    def version(self) -> str:
        """Return the loaded model artifact version.

        Returns:
            Model file stem selected at construction.
        """
        return self._version

    async def predict_out_of_scope(self, jobs: list[SeniorityInput]) -> list[float]:
        """Predict ordered out-of-scope probabilities.

        Args:
            jobs: Ambiguous seniority inputs.

        Returns:
            One probability per input, preserving order.
        """
        if not jobs:
            return []
        return await asyncio.to_thread(self._predict_sync, jobs)

    def _predict_sync(self, jobs: list[SeniorityInput]) -> list[float]:
        texts = [self._embedder.format_input(job.title, job.jd_text) for job in jobs]
        embeddings = self._embedder.embed_batch(texts)
        matrix = np.stack(
            [
                featurize(extract_features(job.title, job.jd_text), embeddings[index])
                for index, job in enumerate(jobs)
            ],
            axis=0,
        )
        import xgboost as xgb  # noqa: PLC0415

        scores = self._booster.predict(xgb.DMatrix(matrix), output_margin=False)
        return [float(score) for score in scores]


def _load_booster(model_path: Path) -> Any:
    import xgboost as xgb  # noqa: PLC0415

    booster = xgb.Booster()
    booster.load_model(str(model_path))
    objective = json.loads(booster.save_config())["learner"]["objective"]["name"]
    if objective != _BINARY_LOGISTIC:
        raise ValueError(
            f"seniority model {model_path.name} has objective {objective!r}; "
            f"expected {_BINARY_LOGISTIC!r}"
        )
    return booster


__all__ = ["XGBoostSeniorityModel"]
