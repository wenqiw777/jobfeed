"""Controlled local trials using frozen rows and existing cached features."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import xgboost as xgb

ROOT = Path("data/sde-gpu-experiments-v1")
OUT = Path("data/sde-xgb-tuning-v1")
CONFIRMED_REPAIR_COUNT = 10


def measure(y, scores, threshold):
    passed = scores >= threshold
    return {
        "recall": float(passed[y == 1].mean()),
        "negative_rejection": float((~passed[y == 0]).mean()),
        "fn": int(((y == 1) & ~passed).sum()),
        "fp": int(((y == 0) & passed).sum()),
        "threshold": float(threshold),
    }


def sweep_variants():
    """Vary one parameter at a time around the regularized candidate."""
    regularized = {"max_depth": 4, "min_child_weight": 8, "reg_lambda": 10}
    variations = (
        ("depth2", "max_depth", 2),
        ("depth5", "max_depth", 5),
        ("depth6", "max_depth", 6),
        ("minchild2", "min_child_weight", 2),
        ("minchild16", "min_child_weight", 16),
        ("lambda1", "reg_lambda", 1),
        ("lambda30", "reg_lambda", 30),
        ("eta004", "eta", 0.04),
        ("colsample05", "colsample_bytree", 0.5),
        ("colsample1", "colsample_bytree", 1.0),
        ("subsample1", "subsample", 1.0),
        ("maxbin512", "max_bin", 512),
    )
    return [
        (f"sweep_{name}", "all", {**regularized, parameter: value})
        for name, parameter, value in variations
    ]


def main():  # noqa: C901 - Keep the finite experiment variants together.
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--extra", action="store_true")
    group.add_argument("--repair", action="store_true")
    group.add_argument("--sweep", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--only", help="Run one candidate from the selected variant group"
    )
    parser.add_argument("--input-dir", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument(
        "--name-suffix", default="", help="Suffix for result/model names"
    )
    args = parser.parse_args()
    if any(c in args.name_suffix for c in ("/", "\\")):
        parser.error("--name-suffix must not contain path separators")
    variants = [
        ("baseline_cpu", "all", {}),
        ("shallow", "all", {"max_depth": 3}),
        (
            "regularized",
            "all",
            {"max_depth": 4, "min_child_weight": 8, "reg_lambda": 10},
        ),
        ("positive_weight", "all", {"scale_pos_weight": 2}),
        ("semantic_structured", "semantic", {}),
        ("text_structured", "text", {}),
    ]
    if args.extra:
        variants = [
            ("slow_learning", "all", {"eta": 0.04}),
            (
                "deeper",
                "all",
                {"max_depth": 8, "eta": 0.05, "min_child_weight": 4, "reg_lambda": 5},
            ),
        ]
    if args.repair:
        variants = [("positive_repair", "all", {})]
    if args.sweep:
        variants = sweep_variants()
    if args.only:
        allowed = [variant[0] for variant in variants]
        if args.only not in allowed:
            parser.error(f"--only must be one of: {', '.join(allowed)}")
        variants = [variant for variant in variants if variant[0] == args.only]
    args.output_dir.mkdir(exist_ok=True, parents=True)
    rows = json.loads((args.input_dir / "rows.json").read_text())
    matrix = sp.load_npz(args.input_dir / "baseline.npz")
    assert matrix.shape[0] == len(rows)
    y = np.array([r["is_sde_job"] for r in rows])
    train = np.array([r["split"] == "train" for r in rows])
    cal = np.array([r["split"] == "calibration" for r in rows])
    repaired_ids = []
    if args.repair:
        confirmed = {
            "435212",
            "411080",
            "41368",
            "443852",
            "439726",
            "416314",
            "409848",
            "400027",
            "14950",
            "411524",
        }
        for i, row in enumerate(rows):
            if train[i] and row["job_id"] in confirmed and y[i] == 0:
                y[i] = 1
                repaired_ids.append(row["job_id"])
        assert len(repaired_ids) == CONFIRMED_REPAIR_COUNT
    for name, feature_group, overrides in variants:
        result_name = name + args.name_suffix
        if (args.output_dir / f"{result_name}.json").exists():
            continue
        x = matrix
        if feature_group == "semantic":
            x = matrix[:, :450]
        elif feature_group == "text":
            x = sp.hstack([matrix[:, :66], matrix[:, 450:]], format="csr")
        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 6,
            "eta": 0.08,
            "subsample": 0.85,
            "colsample_bytree": 0.8,
            "min_child_weight": 2,
            "tree_method": "hist",
            "device": args.device,
            "nthread": 4,
            "max_cached_hist_node": 8,
            "seed": 20260903,
            "scale_pos_weight": float((y[train] == 0).sum() / y[train].sum()),
        }
        params.update(overrides)
        started = time.monotonic()
        dtrain = xgb.DMatrix(x[train], label=y[train], nthread=4)
        dcal = xgb.DMatrix(x[cal], label=y[cal], nthread=4)
        checkpoints = (
            (200, 400, 600)
            if name == "slow_learning"
            else ((160, 260, 400) if name == "deeper" else (80, 160, 260))
        )
        model = xgb.train(
            params,
            dtrain,
            num_boost_round=max(checkpoints),
            evals=[(dcal, "calibration")],
            verbose_eval=50,
        )
        candidates = []
        for rounds in checkpoints:
            scores = model.predict(dcal, iteration_range=(0, rounds))
            for recall in (0.99, 0.995):
                positives = np.sort(scores[y[cal] == 1])
                allowed = int(np.floor((1 - recall) * len(positives) + 1e-9))
                threshold = positives[allowed]
                candidates.append(
                    dict(
                        rounds=rounds,
                        target_recall=recall,
                        **measure(y[cal], scores, threshold),
                    )
                )
        model.save_model(args.output_dir / f"{result_name}.model.json")
        result = {
            "name": result_name,
            "features": x.shape[1],
            "parameters": params,
            "seconds": time.monotonic() - started,
            "calibration": candidates,
            "repaired_training_ids": repaired_ids,
        }
        (args.output_dir / f"{result_name}.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
