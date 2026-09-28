# SDE classifier-only experiment

Scope: offline comparison only; no production model activation or changes to
features, label policy, or eligibility. Research script, not production behavior;
verification uses actual frozen data, predictions and recomputed confusion counts.

Protocol:
- Reassemble existing 11,000 labels; require 5,777 positive / 5,223 negative.
- Freeze title, JD, label and company group before training.
- Preserve existing full-JD hash features and fixed MiniLM 2,000-character input.
- Preserve existing five company-hash folds for all candidates.
- Compare existing XGBoost settings against balanced Logistic Regression and
  Linear SVM with C in 0.1, 1, 10. No extra scaling or feature changes.
- Select linear C and each model's threshold on training OOF predictions only,
  minimizing false positives subject to at least 99% OOF SDE recall.
- OOF metrics are selection metrics, not unbiased final acceptance estimates.
- Evaluate historical 500-row OOS and 500-row challenge collections with frozen
  adjudications. These are reused diagnostics, NOT a new untouched final test.
- Assert no training/holdout job ID or company overlap. Exclude exact normalized
  training-JD duplicates from diagnostic holdouts (same exclusions for all heads).
  Near-duplicate semantic audit is not included in this classifier-only run.
- Compare holdout recall as well as FPR; equal training recall does not guarantee
  identical realized holdout recall. Never tune against holdout outcomes.

Progress:
- [x] Verified original label assembly matches existing 11k model class counts.
- [x] Added isolated experiment runner and isolated local dependency directory.
- [x] Complete shared feature extraction and both alternative model searches.
- [x] Freshly evaluate existing 11k XGBoost baseline on identical holdout matrices.
- [x] Independently recompute counts from saved predictions and review results.

Artifacts: `data/sde-classifier-comparison-v1/`. Script:
`scripts/compare_sde_classifiers.py`.

Data audit findings:
- Original holdouts have 2 OOS and 1 challenge exact-JD training duplicates;
  excluded holdout IDs are recorded in protocol.json. Remaining sizes: 498/499.
- Existing company folds contain 7 exact-JD clusters (14 rows) spanning folds.
  Original folds are preserved for this controlled comparison; OOF selection
  estimates have this residual leakage limitation. A final production acceptance
  would require company + duplicate connected-component folds and a new holdout.

## Outcome

All feature matrices have 33,218 columns. Logistic Regression selects C=10;
Linear SVM selects C=1. Each used 15 fits across three C values and five folds,
then one full-data fit. Both achieve 99.0133% OOF recall (57 false negatives).

| Model | 498-row OOS FP / 145 | OOS FN / 353 | 499-row challenge FP / 182 | Challenge FN / 317 |
| --- | ---: | ---: | ---: | ---: |
| Existing XGBoost | 25 (17.24%) | 4 | 117 (64.29%) | 4 |
| Logistic Regression | 35 (24.14%) | 2 | 126 (69.23%) | 4 |
| Linear SVM | 33 (22.76%) | 2 | 123 (67.58%) | 4 |

Neither tested linear alternative reduced false positives against the existing
baseline. OOS realized recall differs: 98.87% XGBoost vs 99.43% linear models;
thus the OOS FPR difference alone is not a same-recall superiority claim.
Challenge recall matches at 98.74%, but its errors remain high for all heads.
These results do not establish the best possible classifier or identify the
representation/labels as the sole cause. Do not deploy either alternative.

### Execution deviation and limitations

Fresh XGBoost retraining was attempted, then stopped due to prolonged runtime.
A profiler showed histogram construction and a 7.3 GB process footprint; a
second attempt limited cached histogram nodes to 8 and was also stopped. No
completed fresh XGBoost training result is claimed. Baseline version
`v20260904T214830Z` was instead freshly evaluated on the exact same matrices.
Its metadata matches 11,000 total / 5,777 positive and the historical assembly,
but no original training fingerprint exists to prove row-exact identity.
Therefore this is an alternative-head comparison against a historical baseline,
not a completed three-way fresh-retraining ablation.

Completed-run invocation:
`PYTHONPATH=data/sde-classifier-experiment-deps:scripts:src OMP_NUM_THREADS=4 .venv/bin/python scripts/compare_sde_classifiers.py --existing-xgboost`

Direct verification passed for frozen holdout IDs/labels, all six confusion
counts, score-threshold decisions, and both selected linear OOF recalls. No
training process remains. Active SDE configuration is still
`v20260601T170453Z`; no production activation, commit or deployment occurred.
