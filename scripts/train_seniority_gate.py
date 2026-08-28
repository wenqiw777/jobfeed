#!/usr/bin/env python3
"""Train the independent seniority gate from dedicated JSONL labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xgboost as xgb

from jobfeed.adapters.ml._embedder import (
    DEFAULT_MODEL_NAME,
    FastEmbedEmbedder,
)
from jobfeed.adapters.ml._vectorize import EMBEDDING_DIM, featurize
from jobfeed.domain.ml_features import extract_features
from jobfeed.seniority_training import choose_recall_threshold

_FOLDS = 5
_ROUNDS = 120
_BINARY_CLASS_COUNT = 2


@dataclass(frozen=True, slots=True)
class _Label:
    job_id: str
    title: str
    jd_text: str
    value: int


def main() -> None:
    args = _parse_args()
    labels = _load_labels(args.labels)
    matrix = _feature_matrix(labels, args.embedding_model, args.max_chars)
    targets = np.asarray([label.value for label in labels], dtype=np.float32)
    scores = _out_of_fold_scores(labels, matrix, targets)
    threshold = choose_recall_threshold(
        [int(value) for value in targets],
        [float(score) for score in scores],
        minimum_in_scope_recall=args.minimum_in_scope_recall,
    )
    metrics = _metrics(targets, scores, threshold)
    booster = _train(matrix, targets)
    version = datetime.now(UTC).strftime("v%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(args.output_dir / f"{version}.json")
    meta = {
        "version": version,
        "threshold": threshold,
        "embedding_model": args.embedding_model,
        "embedding_dim": EMBEDDING_DIM,
        "train_size": len(labels),
        "positive_count": int(targets.sum()),
        "negative_count": int(len(targets) - targets.sum()),
        "label_source": "deterministic-rule-v1",
        "validation": "5-fold-out-of-fold",
        **metrics,
    }
    (args.output_dir / f"{version}.meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--embedding-model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--minimum-in-scope-recall", type=float, default=0.99)
    return parser.parse_args()


def _load_labels(path: Path) -> list[_Label]:
    labels: list[_Label] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        label = row.get("label")
        if label not in {"in_scope", "out_of_scope"}:
            raise ValueError(f"invalid label at line {line_number}: {label!r}")
        labels.append(
            _Label(
                job_id=str(row["job_id"]),
                title=str(row["title"]),
                jd_text=str(row["jd_text"]),
                value=int(label == "out_of_scope"),
            )
        )
    if len(labels) < _FOLDS * 2:
        raise ValueError("seniority training needs at least 10 labels")
    return labels


def _feature_matrix(
    labels: list[_Label], embedding_model: str, max_chars: int
) -> np.ndarray:
    embedder = FastEmbedEmbedder(model_name=embedding_model, max_chars=max_chars)
    texts = [embedder.format_input(row.title, row.jd_text) for row in labels]
    embeddings = embedder.embed_batch(texts)
    return np.stack(
        [
            featurize(extract_features(row.title, row.jd_text), embeddings[index])
            for index, row in enumerate(labels)
        ],
        axis=0,
    )


def _fold(job_id: str) -> int:
    digest = hashlib.sha256(job_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") % _FOLDS


def _out_of_fold_scores(
    labels: list[_Label], matrix: np.ndarray, targets: np.ndarray
) -> np.ndarray:
    scores = np.zeros(len(labels), dtype=np.float64)
    folds = np.asarray([_fold(label.job_id) for label in labels])
    for fold in range(_FOLDS):
        validation = folds == fold
        training = ~validation
        if not validation.any() or len(set(targets[training])) < _BINARY_CLASS_COUNT:
            raise ValueError(f"fold {fold} lacks validation rows or both classes")
        model = _train(matrix[training], targets[training])
        scores[validation] = model.predict(xgb.DMatrix(matrix[validation]))
    return scores


def _train(matrix: np.ndarray, targets: np.ndarray) -> xgb.Booster:
    return xgb.train(
        {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 4,
            "eta": 0.08,
            "subsample": 0.85,
            "colsample_bytree": 0.8,
            "min_child_weight": 3,
            "seed": 20260826,
        },
        xgb.DMatrix(matrix, label=targets),
        num_boost_round=_ROUNDS,
    )


def _metrics(
    labels: np.ndarray, scores: np.ndarray, threshold: float
) -> dict[str, float]:
    predicted_out = scores >= threshold
    in_scope = labels == 0
    out_scope = labels == 1
    in_recall = float((~predicted_out & in_scope).sum() / in_scope.sum())
    out_recall = float((predicted_out & out_scope).sum() / out_scope.sum())
    precision = float((predicted_out & out_scope).sum() / max(1, predicted_out.sum()))
    return {
        "in_scope_recall": in_recall,
        "out_of_scope_recall": out_recall,
        "out_of_scope_precision": precision,
        "blocked_pct": float(predicted_out.mean()),
    }


if __name__ == "__main__":
    main()
