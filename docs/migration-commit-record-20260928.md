# Migration preparation: local source commits, 2026-09-28

## Purpose and authorization

Preserve remaining project source for a later GitHub clone on Mac mini. This
request authorizes local commits only. No push, merge, deployment, live database
migration or running-service restart was performed.

The user explicitly approved preserving the existing runtime SDE model lexical
hash implementation as a one-time exception to AGENTS.md. The user separately
instructed that hash-bearing offline experiments and their dependent tests stay
local. This exception does not authorize new hash behavior.

## Scope and acceptance

- Commit current runtime code, migrations, API/UI artifacts, independent offline
  tools/tests and planning documents; preserve existing behavior rather than
  repair the entire accumulated worktree during a commit-only request.
- Use explicit file lists; exclude credentials, personal runtime configuration,
  data, generated model versions, logs and database snapshots.
- Completion means the authorized source is represented in local Git and all
  remaining files have an explicit migration or held-experiment disposition.
  It does not mean tests are all green or the application is release-ready.
- Core canonical schema/store/service/API/UI and durable scan changes share
  interfaces and remain together. Independent dev, company CLI, ML runtime,
  browser extension and concurrent status-read behavior have separate commits.

## Current verification

Checks ran on the combined working tree, including the held offline experiments;
individual historical commit snapshots were not independently tested.

| Check | Result |
|---|---|
| Project backend (`pytest -q tests`) | 2659 passed, 13 failed, 477 deselected, 8 xfailed |
| Frontend unit tests | 186 passed |
| Extension tests | 90 passed |
| Frontend production build | passed; existing bundle-size warning |
| Dev and company focused checks | 13 passed |
| Runtime model focused checks | 37 passed |
| Concurrent status reads | 3 passed |
| Included offline script tests | 128 passed |
| Ruff over src/tests/scripts/migrations | 507 errors |
| Ruff format check over same paths | 66 files need formatting |
| Mypy over src | 135 errors in 24 files |
| Git whitespace checks | passed |

PostgreSQL, live-provider, ML-model and browser lanes excluded by default pytest
configuration were not run. No new browser acceptance or screenshot approval
is claimed. Source/test/docs credential-pattern scans and an independent
read-only review found no concrete token/private-key matches; this is a bounded
check, not a guarantee. No source behavior was changed to mask failing checks.
The frontend build regenerated its committed bundle and generated API types.

### Unresolved backend failures

These remain unwaived release blockers. Saving local commits does not accept or
resolve them. Two gold-data tests below are in the explicitly held set and will
not be included in a source-only clone.

- `tests/contract/test_exact_enrichment.py::test_existing_sqlite_adds_retry_columns_without_losing_jobs`
- `tests/integration/test_sqlite_parity_verifier.py::test_exact_fourteen_table_and_aggregate_parity_returns_typed_report`
- `tests/integration/test_sqlite_rollback_source.py::test_snapshot_gates_and_streams_exact_v1_source`
- `tests/integration/test_sqlite_rollback_source.py::test_open_read_snapshot_is_stable_against_later_commits`
- `tests/integration/test_sqlite_schema_lifecycle.py::test_schema_open_backfills_missing_role_types`
- `tests/unit/test_architecture_boundaries.py::test_services_do_not_import_infrastructure_modules`
- `tests/unit/test_cli_ml_gate.py::test_ml_gate_info_prints_version_threshold_and_meta_metrics`
- `tests/unit/test_code_hygiene.py::test_code_hygiene_passes_for_current_production_code`
- `tests/unit/test_gate_spans.py::test_xgboost_gate_under_line_limit`
- `tests/unit/test_jobright_source.py::test_bridge_batch_completes_source_and_reports_progress`
- `tests/unit/test_label_jd_semantic_gold_50.py::test_gold_manifest_and_all_200_field_labels_are_valid`
- `tests/unit/test_label_jd_semantic_gold_50.py::test_claim_evidence_is_an_exact_contiguous_jd_substring`
- `tests/unit/test_timing.py::test_on_progress_callback_called_per_source`

## Local commits before this record

- ea3b054 chore(research): preserve independent JD experiments and maintenance tools
- 7a49a2b feat(jobs): integrate canonical workflows with durable scan and evaluation state
- 710dc26 perf(ml): share in-flight learning status reads safely
- 4b0bdfa fix(extension): recover source frames and retain board discovery progress
- 08bebcd feat(ml): support full-JD SDE model features and authoritative role decisions
- 3252d70 feat(companies): expose company intelligence refresh command
- c53971d fix(dev): keep API stable and recover stale development ports

## Explicitly held offline files

These files and their data must still be copied separately if the migration is
to preserve all local work. Hash-bearing roots and their script/test dependency
closure were retained without deletion or edits.

- `scripts/audit_sde_tuning.py`
- `scripts/compare_sde_classifiers.py`
- `scripts/demo_conservative_blocker_comparison.py`
- `scripts/demo_degree_uncertainty.py`
- `scripts/demo_jd_semantic_50.py`
- `scripts/demo_jd_semantic_double_blind_500.py`
- `scripts/demo_jd_semantic_escalation_50.py`
- `scripts/demo_jd_semantic_fields_50.py`
- `scripts/demo_seniority_1000.py`
- `scripts/demo_seniority_luna_audit.py`
- `scripts/demo_seniority_recent_embeddings.py`
- `scripts/demo_seniority_recent_scan.py`
- `scripts/label_jd_semantic_gold_50.py`
- `scripts/label_sde_dataset.py`
- `scripts/prepare_sde_expansion.py`
- `scripts/report_seniority_recent_scan.py`
- `scripts/review_sde_dataset.py`
- `scripts/train_sde_gate.py`
- `tests/unit/test_demo_conservative_blocker_comparison.py`
- `tests/unit/test_demo_degree_uncertainty.py`
- `tests/unit/test_demo_jd_semantic_50.py`
- `tests/unit/test_demo_jd_semantic_double_blind_500.py`
- `tests/unit/test_demo_jd_semantic_escalation_50.py`
- `tests/unit/test_demo_jd_semantic_fields_50.py`
- `tests/unit/test_label_jd_semantic_gold_50.py`
- `tests/unit/test_label_sde_dataset.py`
- `tests/unit/test_prepare_sde_expansion.py`
- `tests/unit/test_review_sde_dataset.py`
- `tests/unit/test_train_sde_gate.py`

## Separate data migration

- `artifacts/`: 410 untracked files, 9,121,246,138 bytes, including SQLite
  snapshots/WAL/SHM, logs and generated experiment output; excluded wholesale.
- `models/ml_gate/`: 12 untracked generated version/model metadata files,
  3,971,918 bytes; excluded from new source commits and preserved locally.
- Existing ignored `data/`, private config/resume inputs, credentials, browser
  state, required model caches and necessary Redis state need their own inventory
  and consistent transfer. A Git clone alone is not a complete migration.

## Finish disposition

Authorized source preservation can complete despite the recorded failures.
Release readiness and mini cutover remain unverified. Do not delete local files
or stop the Air runtime on the basis of these commits.
