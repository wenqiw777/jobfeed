"""Train a local high-recall candidate model from exploratory Block labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xgboost as xgb


def chunk_text(text, *, size=900, overlap=200):
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size and overlap are invalid")
    chunks = []
    step = size - overlap
    for start in range(0, len(text), step):
        chunks.append(text[start : start + size])
        if start + size >= len(text):
            break
    return chunks or [""]


def document_scores(sample_ids, chunk_scores):
    result = {}
    for sample_id, score in zip(sample_ids, chunk_scores, strict=True):
        result[sample_id] = max(result.get(sample_id, 0.0), float(score))
    return result


def choose_recall_threshold(scores, labels, minimum_recall=0.99):
    positives = sorted(
        (score for sample_id, score in scores.items() if labels[sample_id] == 1),
        reverse=True,
    )
    if not positives or not 0 < minimum_recall <= 1:
        raise ValueError("positive scores and a valid recall target are required")
    required = max(1, int(np.ceil(minimum_recall * len(positives))))
    return float(positives[required - 1])


def _metrics(scores, labels, threshold):
    predictions = {sample_id: score >= threshold for sample_id, score in scores.items()}
    true_positive = sum(predictions[key] and labels[key] == 1 for key in predictions)
    false_positive = sum(predictions[key] and labels[key] == 0 for key in predictions)
    positive = sum(labels[key] == 1 for key in predictions)
    proposed = true_positive + false_positive
    return {
        "documents": len(predictions),
        "teacher_blocks": positive,
        "proposed": proposed,
        "recall_vs_teacher": true_positive / positive if positive else None,
        "precision_vs_teacher": true_positive / proposed if proposed else None,
        "escalation_rate": proposed / len(predictions) if predictions else None,
    }


def _chunk_rows(samples, teacher):
    teacher_by_id = {row["sample_id"]: row for row in teacher}
    rows = []
    for sample in samples:
        decision = teacher_by_id[sample["sample_id"]]
        blocked = decision["decision"] == "block"
        evidence = decision["evidence"]
        metadata_evidence = (
            evidence in sample["title"] or evidence in sample["location"]
        )
        found_positive = False
        for chunk in chunk_text(sample["text"]):
            positive = blocked and (metadata_evidence or evidence in chunk)
            found_positive = found_positive or positive
            rows.append(
                {
                    "sample_id": sample["sample_id"],
                    "text": f"{sample['title']} | {sample['location']} | {chunk}",
                    "label": int(positive),
                }
            )
        if blocked and not found_positive:
            raise ValueError(
                f"teacher Block evidence was not found: {sample['sample_id']}"
            )
    return rows


def _split(sample_id):
    remainder = sample_id % 5
    if remainder == 0:
        return "test"
    if remainder == 1:
        return "development"
    return "train"


def load_samples(input_path, manifest_path):
    inputs = json.loads(input_path.read_text())["samples"]
    manifests = json.loads(manifest_path.read_text())["samples"]
    metadata = {row["sample_id"]: row for row in manifests}
    return [
        {
            "sample_id": row["sample_id"],
            "title": row["document"]["title"],
            "text": row["document"]["text"],
            "location": metadata[row["sample_id"]]["location"],
        }
        for row in inputs
    ]


def embed_texts(texts, device):
    if device == "cuda":
        import torch  # noqa: PLC0415
        from torch.nn import functional  # noqa: PLC0415
        from transformers import AutoModel, AutoTokenizer  # noqa: PLC0415

        model_name = "answerdotai/ModernBERT-base"
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModel.from_pretrained(
            model_name,
            attn_implementation="sdpa",
            reference_compile=False,
        ).to("cuda")
        model.eval()
        batches = []
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            for start in range(0, len(texts), 32):
                encoded = tokenizer(
                    texts[start : start + 32],
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to("cuda")
                hidden = model(**encoded).last_hidden_state.float()
                weights = encoded["attention_mask"].unsqueeze(-1)
                pooled = (hidden * weights).sum(1) / weights.sum(1)
                batches.append(functional.normalize(pooled, dim=1).cpu().numpy())
                completed = min(start + 32, len(texts))
                print(f"embedded {completed}/{len(texts)}", flush=True)
        return np.concatenate(batches).astype(np.float32)

    from jobfeed.adapters.ml._embedder import FastEmbedEmbedder  # noqa: PLC0415

    return FastEmbedEmbedder(max_chars=2600).embed_batch(texts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-out", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    if args.out.exists() or args.model_out.exists():
        parser.error("output path already exists")

    samples = load_samples(args.input, args.manifest)
    comparison = json.loads(args.comparison.read_text())
    teacher = comparison["strategies"]["luna"]
    rows = _chunk_rows(samples, teacher)
    embeddings = embed_texts([row["text"] for row in rows], args.device)
    labels = np.asarray([row["label"] for row in rows], dtype=np.float32)
    splits = np.asarray([_split(row["sample_id"]) for row in rows])

    train = splits == "train"
    model = xgb.train(
        {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 5,
            "eta": 0.08,
            "subsample": 0.85,
            "colsample_bytree": 0.9,
            "tree_method": "hist",
            "device": args.device,
            "scale_pos_weight": max(
                1.0,
                float((labels[train] == 0).sum())
                / max(1.0, float(labels[train].sum())),
            ),
            "seed": 20260905,
        },
        xgb.DMatrix(embeddings[train], label=labels[train]),
        num_boost_round=220,
    )

    document_labels = {
        row["sample_id"]: int(row["decision"] == "block") for row in teacher
    }
    aggregated_by_split = {}
    for split in ("development", "test"):
        selected = splits == split
        selected_ids = [
            row["sample_id"] for row, keep in zip(rows, selected, strict=True) if keep
        ]
        scores = model.predict(xgb.DMatrix(embeddings[selected]))
        aggregated = document_scores(selected_ids, scores)
        aggregated_by_split[split] = aggregated

    operating_points = {}
    for target in (1.0, 0.95, 0.9, 0.8):
        threshold = choose_recall_threshold(
            aggregated_by_split["development"],
            document_labels,
            minimum_recall=target,
        )
        operating_points[str(target)] = {
            "threshold": threshold,
            "development": _metrics(
                aggregated_by_split["development"], document_labels, threshold
            ),
            "test": _metrics(aggregated_by_split["test"], document_labels, threshold),
        }

    report = {
        "teacher": "gpt-5.6-luna exploratory decisions; not human gold",
        "task": "local high-recall candidate proposal; cannot issue Block",
        "chunks": len(rows),
        "operating_points": operating_points,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    model.save_model(args.model_out)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
