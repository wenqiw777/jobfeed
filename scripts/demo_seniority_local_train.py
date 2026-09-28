"""Small offline supervised pilot; Luna labels are not human truth."""

import csv
import json
import random
import re
import time
from pathlib import Path

import numpy as np

from jobfeed.adapters.ml._embedder import FastEmbedEmbedder

ROOT = Path("artifacts/seniority-demo-100")
OUT = ROOT / "local-model"
SEED = 20260918
CLASSIFICATION_THRESHOLD = 0.5


def norm(s):
    return re.sub(r"\s+", " ", s).strip().casefold()


def chunks(text, size=400):
    # All characters are covered; no prefix-only JD truncation.
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -40, 40)))


def fit(x, y):
    # Fixed L2 penalty and optimizer; do not tune against test predictions.
    w = np.zeros(x.shape[1])
    b = float(np.log((y.sum() + 1) / (len(y) - y.sum() + 1)))
    for _ in range(1200):
        error = sigmoid(x @ w + b) - y
        w -= 1.0 * (x.T @ error / len(y) + 0.01 * w)
        b -= float(error.mean())
    return w, b


def choose_thresholds(p, y):
    # Strict inequalities; endpoints mean abstain on that side.
    lows = [0.0] + [float(v) for v in np.unique(p)]
    highs = [1.0] + [float(v) for v in np.unique(p)]
    low = max(t for t in lows if not np.any((p < t) & (y == 1)))
    high = min(t for t in highs if not np.any((p > t) & (y == 0)))
    if low >= high:
        low = high = (low + high) / 2
    return low, high


def metrics(p, y, low, high):
    predicted = p >= CLASSIFICATION_THRESHOLD
    auto_keep = p < low
    auto_block = p > high
    return {
        "n": len(y),
        "teacher_keep": int((y == 0).sum()),
        "teacher_block": int((y == 1).sum()),
        "correct_at_05": int((predicted == y).sum()),
        "false_block_at_05": int((predicted & (y == 0)).sum()),
        "missed_block_at_05": int((~predicted & (y == 1)).sum()),
        "auto_keep": int(auto_keep.sum()),
        "auto_block": int(auto_block.sum()),
        "defer": int((~(auto_keep | auto_block)).sum()),
        "false_auto_block": int((auto_block & (y == 0)).sum()),
        "missed_auto_keep": int((auto_keep & (y == 1)).sum()),
    }


def _grouped_split(jobs, labels):
    # Union company and exact text groups without adding a content digest.
    n = len(jobs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            i = parent[i]
        return i

    def union(a, b):
        parent[find(a)] = find(b)

    normalized_companies = [norm(j["company"]) for j in jobs]
    normalized_texts = [norm(j["jd_text"]) for j in jobs]
    for i in range(n):
        for j in range(i):
            if (
                normalized_companies[i] == normalized_companies[j]
                or normalized_texts[i] == normalized_texts[j]
            ):
                union(i, j)
    eligible = [
        i
        for i, r in enumerate(labels)
        if r["decision"] != "uncertain" and r["evidence_valid"]
    ]
    groups = sorted({find(i) for i in eligible})
    random.Random(SEED).shuffle(groups)
    # Allocate by group count, never search seeds using labels.
    a = int(len(groups) * 0.6)
    b = int(len(groups) * 0.8)
    split_groups = {
        "train": set(groups[:a]),
        "dev": set(groups[a:b]),
        "test": set(groups[b:]),
    }
    indices = {
        k: [i for i in eligible if find(i) in g] for k, g in split_groups.items()
    }
    for k, split_indices in indices.items():
        assert {labels[i]["decision"] for i in split_indices} == {"keep", "block"}, (
            f"{k} lacks a class"
        )
    return eligible, groups, indices


def main():
    OUT.mkdir(exist_ok=True)
    jobs = json.loads((ROOT / "results.json").read_text())
    labels = json.loads((ROOT / "luna-audit/results.json").read_text())
    assert [j["sample_id"] for j in jobs] == [r["sample_id"] for r in labels]
    n = len(jobs)
    eligible, groups, indices = _grouped_split(jobs, labels)
    manifests = [
        {
            "sample_id": j["sample_id"],
            "id": j["id"],
            "company": j["company"],
            "split": next((k for k, v in indices.items() if i in v), "challenge"),
            "teacher": labels[i]["decision"],
            "evidence_valid": labels[i]["evidence_valid"],
        }
        for i, j in enumerate(jobs)
    ]
    (OUT / "splits.json").write_text(
        json.dumps(manifests, ensure_ascii=False, indent=2)
    )
    inputs = []
    spans = []
    for j in jobs:
        start = len(inputs)
        inputs.append(j["title"])
        inputs.extend(chunks(j["jd_text"]))
        spans.append((start, len(inputs)))
    input_path = OUT / "embedding_inputs.json"
    if (OUT / "embeddings.npy").exists():
        assert json.loads(input_path.read_text()) == inputs, (
            "Embedding inputs changed; use a new output directory"
        )
    else:
        input_path.write_text(json.dumps(inputs, ensure_ascii=False))
    print(
        "SPLITS",
        {k: len(v) for k, v in indices.items()},
        "texts",
        len(inputs),
        flush=True,
    )
    cache = OUT / "embeddings.npy"
    t = time.perf_counter()
    if cache.exists():
        emb = np.load(cache)
        assert emb.shape == (len(inputs), 384)
        embed_seconds = None
    else:
        model = FastEmbedEmbedder(max_chars=max(map(len, inputs)))
        batches = []
        for start in range(0, len(inputs), 128):
            batches.append(model.embed_batch(inputs[start : start + 128]))
            print("EMBED", min(start + 128, len(inputs)), len(inputs), flush=True)
        emb = np.concatenate(batches)
        np.save(cache, emb)
        embed_seconds = time.perf_counter() - t
    title = np.stack([emb[a] for a, b in spans])
    body = np.stack([emb[a + 1 : b].mean(axis=0) for a, b in spans])
    body /= np.maximum(np.linalg.norm(body, axis=1, keepdims=True), 1e-12)
    arrays = {
        "title_only": title,
        "title_plus_full_jd": np.concatenate([title, body], axis=1) / np.sqrt(2),
    }
    y = np.array([int(r["decision"] == "block") for r in labels])
    results = {}
    predictions = []
    for name, x in arrays.items():
        t = time.perf_counter()
        w, intercept = fit(x[indices["train"]], y[indices["train"]])
        train_seconds = time.perf_counter() - t
        t = time.perf_counter()
        p = sigmoid(x @ w + intercept)
        predict_seconds = time.perf_counter() - t
        low, high = choose_thresholds(p[indices["dev"]], y[indices["dev"]])
        np.savez(
            OUT / f"{name}.npz",
            weights=w,
            intercept=intercept,
            auto_keep_below=low,
            auto_block_above=high,
        )
        res = {
            "train_seconds": train_seconds,
            "predict_all_seconds": predict_seconds,
            "auto_keep_below": low,
            "auto_block_above": high,
        }
        for split, ix in indices.items():
            res[split] = metrics(p[ix], y[ix], low, high)
        difficult = [i for i in indices["test"] if jobs[i]["decision"] == "uncertain"]
        res["test_original_uncertain"] = metrics(p[difficult], y[difficult], low, high)
        results[name] = res
        for i, j in enumerate(jobs):
            predictions.append(
                {
                    "model": name,
                    "sample_id": j["sample_id"],
                    "id": j["id"],
                    "company": j["company"],
                    "title": j["title"],
                    "split": manifests[i]["split"],
                    "baseline": j["decision"],
                    "teacher": labels[i]["decision"],
                    "probability": float(p[i]),
                    "local_decision": "keep"
                    if p[i] < low
                    else "block"
                    if p[i] > high
                    else "defer",
                }
            )
    summary = {
        "seed": SEED,
        "source_jobs": n,
        "supervised_jobs": len(eligible),
        "challenge_jobs": n - len(eligible),
        "groups": len(groups),
        "split_sizes": {k: len(v) for k, v in indices.items()},
        "embedding_model": "all-MiniLM-L6-v2",
        "text_chunks": len(inputs),
        "embedding_seconds": embed_seconds,
        "results": results,
        "limitations": [
            "Teacher imitation, not human accuracy",
            "Frozen grouped cohort; see sampling manifest",
            "Small grouped test set",
            "Mean pooling does not explicitly solve AND/OR relations",
            "Confidence scores are not calibrated correctness probabilities",
            "No production changes; training itself makes no Luna calls",
        ],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    (OUT / "predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2)
    )
    with (OUT / "predictions.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(predictions[0]))
        writer.writeheader()
        writer.writerows(predictions)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    ROOT = args.root
    OUT = ROOT / "local-model"
    main()
