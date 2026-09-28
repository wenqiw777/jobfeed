#!/usr/bin/env python3
"""Evaluate an SDE model on labels that were never passed to training."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jobfeed.adapters.ml.xgboost_gate import XGBoostGate
from jobfeed.ports.ml_gate import GateInput


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--model-dir", type=Path, default=Path("models/ml_gate"))
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--errors-output", type=Path)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    rows = _read_jsonl(args.input)
    if args.adjudications:
        decisions = {
            str(row["job_id"]): bool(row["is_sde_job"])
            for row in _read_jsonl(args.adjudications)
        }
        rows = apply_adjudications(rows, decisions)
    gate = XGBoostGate(
        model_dir=args.model_dir,
        model_version=args.model_version,
    )
    predictions: list[bool] = []
    errors: list[dict[str, object]] = []
    for offset in range(0, len(rows), 256):
        batch = rows[offset : offset + 256]
        results = await gate.predict_batch(
            [
                GateInput(
                    job_id=str(row["job_id"]),
                    title=str(row["title"]),
                    jd_text=str(row["jd_text"]),
                )
                for row in batch
            ]
        )
        for row, result in zip(batch, results, strict=True):
            prediction = result.result == "pass"
            predictions.append(prediction)
            if prediction != bool(row["is_sde_job"]):
                errors.append(
                    {
                        **row,
                        "model_prediction": prediction,
                        "model_score": result.score,
                        "model_version": result.version,
                    }
                )
    if args.errors_output:
        args.errors_output.parent.mkdir(parents=True, exist_ok=True)
        args.errors_output.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in errors),
            encoding="utf-8",
        )
    metrics = classification_metrics(
        labels=[bool(row["is_sde_job"]) for row in rows],
        predictions=predictions,
    )
    print(json.dumps({**metrics, "model_version": args.model_version}, indent=2))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def apply_adjudications(
    rows: list[dict[str, object]], decisions: dict[str, bool]
) -> list[dict[str, object]]:
    return [
        {
            **row,
            "is_sde_job": decisions.get(str(row["job_id"]), row["is_sde_job"]),
        }
        for row in rows
    ]


def classification_metrics(
    *, labels: list[bool], predictions: list[bool]
) -> dict[str, int | float]:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have the same size")
    positive = sum(labels)
    negative = len(labels) - positive
    true_positive = sum(
        label and prediction
        for label, prediction in zip(labels, predictions, strict=True)
    )
    false_positive = sum(
        not label and prediction
        for label, prediction in zip(labels, predictions, strict=True)
    )
    false_negative = sum(
        label and not prediction
        for label, prediction in zip(labels, predictions, strict=True)
    )
    return {
        "size": len(labels),
        "positive": positive,
        "negative": negative,
        "sde_recall": true_positive / max(1, positive),
        "sde_precision": true_positive / max(1, true_positive + false_positive),
        "non_sde_recall": (negative - false_positive) / max(1, negative),
        "false_positive_rate": false_positive / max(1, negative),
        "false_negative_rate": false_negative / max(1, positive),
    }


if __name__ == "__main__":
    main()
