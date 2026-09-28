"""Compare completed candidates at calibration-selected operating points."""

import json
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import xgboost as xgb

from jobfeed.domain.ml_features import clearly_nonsoftware_title
from scripts.tune_sde_xgb import measure

OUT = Path("data/sde-xgb-tuning-v1")
ROOT = Path("data/sde-gpu-experiments-v1")
SEMANTIC_STRUCTURED_DIM = 450
BINARY_CLASS_COUNT = 2


def select_columns(matrix, count):
    if count == SEMANTIC_STRUCTURED_DIM:
        return matrix[:, :SEMANTIC_STRUCTURED_DIM]
    if count == matrix.shape[1] - 384:
        return sp.hstack([matrix[:, :66], matrix[:, 450:]], format="csr")
    assert count == matrix.shape[1]
    return matrix


def audit_group_metrics(scores, groups, threshold, rule_blocked_ids=()):
    """Count decisions for the existing independently reviewed groups."""
    passed = {
        job_id: score >= threshold and job_id not in rule_blocked_ids
        for job_id, score in scores.items()
    }
    return {
        group: {
            "rows": len(jobs),
            "passed": sum(passed[j] for j in jobs),
            "blocked": sum(not passed[j] for j in jobs),
            "blocked_ids": [j for j in jobs if not passed[j]],
        }
        for group, jobs in groups.items()
    }


def broad_comparison(reports, reviews, checkpoint_predictions=None):
    """Adapt thresholds to reviewed keep cases; these are development metrics."""
    audit = json.loads((OUT / "independent-audit.json").read_text())
    groups = {
        name: audit[name]
        for name in (
            "confirmed_software_related_keep",
            "uncertain_or_adjacent_keep",
            "clearly_unrelated",
        )
    }
    candidates = (
        checkpoint_predictions
        if checkpoint_predictions is not None
        else {
            (r["name"], r["calibration"]["rounds"]): r["fresh_predictions"]
            for r in reports
        }
    )
    candidates[("existing_11k", None)] = json.loads(
        (OUT / "existing-11k-fresh-predictions.json").read_text()
    )
    high = [r for r in reviews.values() if r["confidence"] == "high"]
    y = np.array([int(r["is_sde_job"]) for r in high])
    fresh_rows = [
        json.loads(line)
        for line in (OUT / "fresh-review-input.jsonl").read_text().splitlines()
    ]
    rule_blocked_ids = {
        r["job_id"] for r in fresh_rows if clearly_nonsoftware_title(r["title"])
    }
    high_rule_blocked = np.array([r["job_id"] in rule_blocked_ids for r in high])
    results = []
    for (name, rounds), predictions in candidates.items():
        scores = {r["job_id"]: r["score"] for r in predictions}
        assert len(scores) == len(predictions)
        fresh_scores = np.array([scores[r["job_id"]] for r in high])
        keep = groups["confirmed_software_related_keep"]
        for policy, ids in (
            ("confirmed_keep", keep),
            (
                "confirmed_and_borderline_keep",
                keep + groups["uncertain_or_adjacent_keep"],
            ),
        ):
            threshold = min(scores[j] for j in ids)
            results.append(
                {
                    "name": name,
                    "rounds": rounds,
                    "policy": policy,
                    "threshold": threshold,
                    "limiting_ids": [j for j in ids if scores[j] == threshold],
                    "audit_groups": audit_group_metrics(scores, groups, threshold),
                    "fresh_high_confidence": {
                        "rows": len(high),
                        "positives": int(y.sum()),
                        "negatives": int((y == 0).sum()),
                        **measure(y, fresh_scores, threshold),
                    },
                    "model_plus_rules": {
                        "audit_groups": audit_group_metrics(
                            scores, groups, threshold, rule_blocked_ids
                        ),
                        "fresh_high_confidence": {
                            "rows": len(high),
                            "positives": int(y.sum()),
                            "negatives": int((y == 0).sum()),
                            **measure(
                                y,
                                np.where(high_rule_blocked, -np.inf, fresh_scores),
                                threshold,
                            ),
                        },
                        "rule_blocked_ids": sorted(rule_blocked_ids),
                        "newly_blocked_ids": sorted(
                            j for j in rule_blocked_ids if scores[j] >= threshold
                        ),
                        "confirmed_keep_rule_blocked_ids": sorted(
                            rule_blocked_ids.intersection(keep)
                        ),
                    },
                }
            )
    return {
        "evaluation_role": "development_adaptation_not_independent_acceptance",
        "audit_group_counts": {name: len(ids) for name, ids in groups.items()},
        "notes": (
            "Thresholds use reviewed keep cases already present in the fresh sample. "
            "The audit is targeted, not a random accuracy estimate. "
            "Borderline keep cases remain separate from occupation labels. "
            "Existing metrics use model scores only. model_plus_rules also applies "
            "the current narrow clearly_nonsoftware_title rule to fresh titles."
        ),
        "candidates": results,
    }


def main():
    rows = json.loads((ROOT / "rows.json").read_text())
    matrix = sp.load_npz(ROOT / "baseline.npz")
    fresh = sp.load_npz(OUT / "fresh-features.npz")
    fresh_ids = json.loads((OUT / "fresh-feature-ids.json").read_text())
    reviews = {
        r["job_id"]: r
        for r in map(
            json.loads, (OUT / "fresh-sol-review.jsonl").read_text().splitlines()
        )
    }
    reports = []
    checkpoint_predictions = {}
    for path in sorted(OUT.glob("*.json")):
        if path.name.endswith(".model.json"):
            continue
        obj = json.loads(path.read_text())
        if not isinstance(obj, dict) or "calibration" not in obj:
            continue
        model = xgb.Booster()
        model.load_model(OUT / f"{obj['name']}.model.json")
        fresh_matrix = xgb.DMatrix(select_columns(fresh, obj["features"]), nthread=4)
        for rounds in sorted({r["rounds"] for r in obj["calibration"]}):
            checkpoint_scores = model.predict(fresh_matrix, iteration_range=(0, rounds))
            checkpoint_predictions[(obj["name"], rounds)] = [
                {"job_id": j, "score": float(s)}
                for j, s in zip(fresh_ids, checkpoint_scores, strict=True)
            ]
        for target in (0.99, 0.995):
            options = [r for r in obj["calibration"] if r["target_recall"] == target]
            chosen = max(options, key=lambda r: (r["negative_rejection"], -r["rounds"]))
            threshold = chosen["threshold"]
            scores = model.predict(
                xgb.DMatrix(select_columns(matrix, obj["features"]), nthread=4),
                iteration_range=(0, chosen["rounds"]),
            )
            fscore = model.predict(
                xgb.DMatrix(select_columns(fresh, obj["features"]), nthread=4),
                iteration_range=(0, chosen["rounds"]),
            )
            result = {
                "name": obj["name"],
                "features": obj["features"],
                "calibration": chosen,
                "splits": {},
            }
            for split in ("test", "diagnostic_oos", "diagnostic_validation"):
                indices = [i for i, r in enumerate(rows) if r["split"] == split]
                y = np.array([rows[i]["is_sde_job"] for i in indices])
                result["splits"][split] = measure(y, scores[indices], threshold)
            for stratum in ("random", "challenge"):
                indices = [
                    i
                    for i, j in enumerate(fresh_ids)
                    if j in reviews
                    and reviews[j]["confidence"] == "high"
                    and reviews[j]["audit_stratum"] == stratum
                ]
                y = np.array(
                    [int(reviews[fresh_ids[i]]["is_sde_job"]) for i in indices]
                )
                if indices and len(set(y)) == BINARY_CLASS_COUNT:
                    result["splits"]["fresh_" + stratum + "_high_confidence"] = dict(
                        rows=len(indices), **measure(y, fscore[indices], threshold)
                    )
            result["fresh_predictions"] = [
                {"job_id": j, "score": float(s), "passed": bool(s >= threshold)}
                for j, s in zip(fresh_ids, fscore, strict=True)
            ]
            reports.append(result)
    (OUT / "comparison.json").write_text(json.dumps(reports, indent=2) + "\n")
    (OUT / "broad-comparison.json").write_text(
        json.dumps(broad_comparison(reports, reviews, checkpoint_predictions), indent=2)
        + "\n"
    )
    for r in reports:
        print(r["name"], r["calibration"], r["splits"], flush=True)


if __name__ == "__main__":
    main()
