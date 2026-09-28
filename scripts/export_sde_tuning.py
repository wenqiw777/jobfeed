"""Export two local candidates and verify the actual gate on reviewed fresh JDs."""

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import xgboost as xgb

from jobfeed.adapters.ml.xgboost_gate import XGBoostGate
from jobfeed.domain.ml_features import clearly_nonsoftware_title
from jobfeed.ports.ml_gate import GateInput

ROOT = Path("data/sde-xgb-tuning-v1")
FROZEN = Path("data/sde-gpu-experiments-v1")
OUTPUT = ROOT / "candidates"
FEATURE_COUNT = 33218
CANDIDATES = (("regularized", 80, 0.05), ("sweep_depth6_gpu", 160, 0.04))
GROUPS = (
    "confirmed_software_related_keep",
    "uncertain_or_adjacent_keep",
    "clearly_unrelated",
)


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def metrics(labels, passed):
    labels = np.asarray(labels, dtype=bool)
    passed = np.asarray(passed, dtype=bool)
    tp = int((labels & passed).sum())
    tn = int((~labels & ~passed).sum())
    fp = int((~labels & passed).sum())
    fn = int((labels & ~passed).sum())
    recall = tp / max(1, tp + fn)
    precision = tp / max(1, tp + fp)
    return {
        "rows": len(labels),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "recall": recall,
        "precision": precision,
        "negative_rejection": tn / max(1, tn + fp),
        "f1": 2 * recall * precision / max(1e-12, recall + precision),
    }


def export_model(candidate, frozen_rows, baseline, version):
    name, rounds, threshold = candidate
    source = xgb.Booster()
    source.load_model(ROOT / f"{name}.model.json")
    source.set_param({"device": "cpu", "nthread": 4})
    assert source.num_features() == FEATURE_COUNT
    sliced = source[:rounds]
    directory = OUTPUT / name
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / f"{version}.json"
    if model_path.exists():
        raise FileExistsError(model_path)
    sliced.save_model(model_path)
    loaded = xgb.Booster()
    loaded.load_model(model_path)
    loaded.set_param({"device": "cpu", "nthread": 4})
    assert loaded.num_features() == FEATURE_COUNT
    assert loaded.num_boosted_rounds() == rounds
    cal_ids = [i for i, row in enumerate(frozen_rows) if row["split"] == "calibration"]
    cal_matrix = xgb.DMatrix(baseline[cal_ids], nthread=4)
    cal_scores = loaded.predict(cal_matrix)
    np.testing.assert_allclose(
        cal_scores,
        source.predict(cal_matrix, iteration_range=(0, rounds)),
        rtol=0,
        atol=1e-6,
    )
    cal = metrics(
        [frozen_rows[i]["is_sde_job"] for i in cal_ids], cal_scores >= threshold
    )
    train = [r for r in frozen_rows if r["split"] == "train"]
    meta = json.loads(Path("models/ml_gate/v20260904T214830Z.meta.json").read_text())
    meta.update(
        version=version,
        threshold=threshold,
        rounds=rounds,
        train_size=len(train),
        pos_count=sum(r["is_sde_job"] for r in train),
        neg_count=sum(not r["is_sde_job"] for r in train),
        recall_pos=cal["recall"],
        precision_pos=cal["precision"],
        f1=cal["f1"],
        non_sde_recall=cal["negative_rejection"],
        blocked_pct=(cal["tn"] + cal["fn"]) / cal["rows"],
        label_provenance=(
            "frozen-original-luna-labels; Sol and independent JD reviews "
            "used for development only"
        ),
        validation=(
            "metadata metrics: original-label calibration; "
            "threshold adapted on reviewed development cases"
        ),
    )
    (directory / f"{version}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return directory, loaded, cal


def verify_results(actual, rows, expected_scores, expected_passed):
    assert len(actual) == len(rows)
    scores = np.array([r.score for r in actual])
    passed = np.array([r.result == "pass" for r in actual])
    np.testing.assert_allclose(scores, expected_scores, rtol=0, atol=1e-6)
    np.testing.assert_array_equal(passed, expected_passed)
    assert all(r.is_swe_role == bool(p) for r, p in zip(actual, passed, strict=True))
    return float(np.max(np.abs(scores - expected_scores)))


async def main():
    rows = read_lines(ROOT / "fresh-review-input.jsonl")
    ids = [r["job_id"] for r in rows]
    assert ids == json.loads((ROOT / "fresh-feature-ids.json").read_text())
    assert len(ids) == len(set(ids))
    fresh = sp.load_npz(ROOT / "fresh-features.npz")
    assert fresh.shape == (len(rows), FEATURE_COUNT)
    reviews = {r["job_id"]: r for r in read_lines(ROOT / "fresh-sol-review.jsonl")}
    audit = json.loads((ROOT / "independent-audit.json").read_text())
    frozen_rows = json.loads((FROZEN / "rows.json").read_text())
    baseline = sp.load_npz(FROZEN / "baseline.npz")
    inputs = [
        GateInput(job_id=r["job_id"], title=r["title"], jd_text=r["jd_text"])
        for r in rows
    ]
    blocked = np.array([clearly_nonsoftware_title(r["title"]) for r in rows])
    high = [
        i for i, job_id in enumerate(ids) if reviews[job_id]["confidence"] == "high"
    ]
    labels = [reviews[ids[i]]["is_sde_job"] for i in high]
    version = datetime.now(UTC).strftime("v%Y%m%dT%H%M%SZ")
    states = []
    for name, rounds, threshold in CANDIDATES:
        directory, model, calibration = export_model(
            (name, rounds, threshold), frozen_rows, baseline, version
        )
        model_scores = model.predict(xgb.DMatrix(fresh, nthread=4))
        expected_scores = np.where(blocked, 0.0, model_scores)
        expected_passed = (model_scores >= threshold) & ~blocked
        gate = XGBoostGate(model_dir=directory, model_version=version)
        started = time.perf_counter()
        actual = await gate.predict_batch(inputs)
        initial_seconds = time.perf_counter() - started
        difference = verify_results(actual, rows, expected_scores, expected_passed)
        by_id = dict(zip(ids, actual, strict=True))
        mistakes = [
            {
                "job_id": ids[i],
                "title": rows[i]["title"],
                "label": bool(reviews[ids[i]]["is_sde_job"]),
                "passed": actual[i].result == "pass",
                "score": actual[i].score,
                "review_reason": reviews[ids[i]]["reason"],
            }
            for i in high
            if (actual[i].result == "pass") != bool(reviews[ids[i]]["is_sde_job"])
        ]
        groups = {
            group: {
                "rows": len(audit[group]),
                "passed": sum(by_id[j].result == "pass" for j in audit[group]),
                "blocked_ids": [j for j in audit[group] if by_id[j].result != "pass"],
            }
            for group in GROUPS
        }
        report = {
            "name": name,
            "version": version,
            "rounds": rounds,
            "threshold": threshold,
            "model_path": str((directory / f"{version}.json").resolve()),
            "feature_count": FEATURE_COUNT,
            "verified_rows": len(rows),
            "max_score_difference": difference,
            "decisions_match": True,
            "evaluation_role": (
                "reviewed development cases, not independent acceptance accuracy"
            ),
            "calibration_original_labels": calibration,
            "fresh_high_confidence": metrics(
                labels, [actual[i].result == "pass" for i in high]
            ),
            "audit_groups": groups,
            "rule_blocked_ids": [ids[i] for i in np.flatnonzero(blocked)],
            "mistakes": mistakes,
            "initial_load_and_inference_seconds": initial_seconds,
            "timing_scope": (
                "300 complete input JDs; structured extraction, default real "
                "embedding, lexical vectorization, model prediction; "
                "both candidates warmed before timed rounds"
            ),
            "timings": [],
        }
        states.append((gate, expected_scores, expected_passed, directory, report))
        print(
            json.dumps(
                {
                    "validated": name,
                    "seconds": initial_seconds,
                    "fresh": report["fresh_high_confidence"],
                }
            ),
            flush=True,
        )
    for round_number, order in enumerate((states, list(reversed(states))), 1):
        for gate, scores, passed, _directory, report in order:
            started = time.perf_counter()
            actual = await gate.predict_batch(inputs)
            seconds = time.perf_counter() - started
            verify_results(actual, rows, scores, passed)
            timing = {
                "round": round_number,
                "seconds": seconds,
                "rows_per_second": len(rows) / seconds,
            }
            report["timings"].append(timing)
            print(json.dumps({"timed": report["name"], **timing}), flush=True)
    for _, _, _, directory, report in states:
        (directory / "acceptance.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
    (OUTPUT / "acceptance.json").write_text(
        json.dumps([state[-1] for state in states], indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
