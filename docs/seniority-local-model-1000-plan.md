# 1000-job local seniority experiment

User authorized a larger sample. Freeze 1000 random current software-role-filter pass records with nonempty JDs, excluding previous100 IDs and exact normalized JD texts. Same Luna policy, blind to rule outputs; 20 JDs/call,4 concurrent calls, no production mutation. Raw outputs preserved; uncertain/invalid evidence labels excluded from supervised metrics, exclusion counts reported.

Same model and hyperparameters as pilot: MiniLM title plus full-JD short-chunk mean embeddings, L2 logistic classifier, title-only control. Group companies and identical JDs before train/dev/test. Development chooses abstention thresholds. Test locked against subsequent tuning. Evaluate original-rule uncertain subset separately. No new hashes. This is teacher imitation, not human accuracy.

Acceptance:1000 terminal label outputs, all ID/evidence validation checks, training/validation/test metrics, raw false-block cases read against source, local keep-only routing savings and errors, saved model reproduction. No hidden retries correcting semantic labels; no test tuning.

Completed 2026-09-18:
-1000 fresh input records,548 company names.50 completed Luna batches:545 keep/429 block/26 uncertain;94 invalid-evidence outputs,115 excluded in union;885 supervised labels.
-535train/176dev/174test company/exact-text grouped. Same classifier hyperparameters as pilot, no test tuning.
-Full-JD test:34 auto-keep,6 auto-block,134 defer,0 false auto-block vsLuna,4 false auto-keep. All4 source requirements reviewed and support exclusion under bachelor's/0–3year policy.
-Original-uncertain test subset91:16 auto-keep,0 auto-block,75 defer,4 false auto-keep. Not ready for routing or filtering.
-Local embedding793.9s; classifier training3.05s; Luna labeling624.8s with4concurrent calls.
-Direct verification passed for input/label IDs, quotations, group/text split isolation, full-text input coverage, cached input equality, serialized model reproduction and independently recalculated split metrics.
-Final report: artifacts/seniority-demo-1000/local-model/REPORT.md. No production changes.
