# Local seniority classifier pilot

Scope: offline exploratory demo only. Reuse frozen 100 JDs and blind Luna labels; no production/config/status changes, no additional Luna calls, no hashes. Exclude uncertain and evidence-invalid teacher labels from supervised metrics. Retain them as an unscored challenge set.

Protocol: deterministic company-group train/development/test split; exact normalized duplicate texts must not cross splits. Train only on training labels, choose auto-keep/auto-block thresholds only on development. Keep test results unchanged after first evaluation. The cohort was previously inspected, so this is a held-out pilot within a development cohort, not an untouched external benchmark or human gold.

Models: local MiniLM embeddings with a small L2 logistic classifier, compared with a title-only logistic control and deterministic title rules. Full JD represented by all short chunks (mean pooling), title embedded separately. No label-derived evidence snippets used as features. Fixed hyperparameters; no test tuning.

Acceptance evidence: input IDs/splits, training/embedding runtime, serialized weights, held-out confusion counts against Luna, auto-decision coverage and error counts, separate old-uncertain subset. Reproduction checks use direct IDs, texts and shapes. Exploration exception to test-first production behavior requirements: no production behavior changes.

Completed:
- 85 eligible teacher labels split 53 train / 17 dev / 15 test, grouped by company and exact JD text; 15 challenge rows excluded from supervised metrics.
- MiniLM embedding runtime 22.74 seconds; fixed logistic training 0.051 seconds.
- Full-JD pilot test: 8 auto-keep, 5 auto-block, 2 defer. One auto-block disagrees with Luna keep (#79); original JD confirms an in-scope 2–4 year pathway. No test tuning performed.
- Title-only control repeats the same false block. Difficult test subset has only4 rows, all teacher keep; insufficient evidence of difficult-block detection.
- Verified group/text isolation, input character coverage and saved-weight score reproduction.
- Final report artifacts/seniority-demo-100/local-model/REPORT.md. Disposition: no production activation; promising as keep-only router, not proven enough for hard filtering.
