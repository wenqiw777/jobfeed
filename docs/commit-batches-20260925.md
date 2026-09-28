# Local commit batches, 2026-09-25

User scope: commit completed feature changes in functional batches. Preserve
Indeed and LinkedIn Guest. Keep raw databases, logs, model artifacts and
unfinished experiments local. No push or merge authorized.

## Committed

- 66f58fe: nonsoftware-role filtering and feature helpers.
- 7ee789e: company background evidence collection and cache.
- de7d9c5: posting identity and canonical evidence matching primitives.
- 6264f1d: eligibility, posting age and priority rules.
- f2e86b6: bounded exact evaluation-request reuse helper.
- b45c965: original-page text and explicit repost extraction.
- 6c6f866: browser extension collectors and extraction workers.
- 97a0c9a: source adapters, bridge and durable journal support.

These are local commits, not a completed release. Integration wiring and UI
changes still remain in the working tree. Verification below used the current
combined working tree; individual historical commits were not separately built.

## Current verification

- UI: 180 tests passed across 19 test files.
- Extension: 79 tests passed.
- Project Python suite (`pytest -q tests`): 2616 passed, 12 failed,
  477 deselected, 8 xfailed. PostgreSQL/live/browser/ML-model lanes excluded by
  the existing pytest configuration.
- Focused source tests: 88 passed.
- Company background tests: 5 passed.
- Eligibility/priority/age tests: 51 passed.
- Page extraction tests: 14 passed.
- Earlier source UI checks: build and lint passed; live local UI screenshots
  inspected. No live settings changes or new scans performed by this task.
- The initial unbounded pytest invocation collected vendored experiment package
  tests under data and failed collection; the project-scoped run supersedes it.

## Outstanding findings and decisions

Python failures: rollback manifest revision expectation; domain import allowlist;
service-to-infrastructure import boundary; CLI model test influenced by local
config; code hygiene; XGBoost file length; two experimental gold-data consistency
tests; two session-source error expectations; progress callback count; retry API.
They have not been fixed or waived, and the full suite must not be described as
green.

Evidence Fit was retired by the two-stage restoration recorded in
restore-two-stage-plan.md. Current evaluation entry points call
parse_stage_b_response; parse_evidence_fit_response and
JobPriorityProjectionBuilder have no callers under src/jobfeed. Their leftover
experimental implementations are excluded from completed feature batches.
Preserve existing compatibility storage and historical data; do not restore the
retired workflow or perform a destructive schema cleanup as part of committing.

The uncommitted ML feature implementation includes hash calculations required
by the current trained model. AGENTS.md prohibits new hash behavior without
explicit user authorization; the earlier question has not been answered.
The inactive priority snapshot fingerprint and Evidence Fit metadata are not
requirements of the active evaluation workflow. Preserve pending files without
treating those remnants as completed features to ship.

## Dead-code cleanup — 2026-09-26

User authorized removal after three read-only subagent audits.
- Removed inactive Evidence Fit generation/parser/template and Store write
  branches, including the unreachable Postgres Stage A branch. Kept historical
  JSON readers, current A/B fields and offline training readers.
- Removed old priority snapshot builder, models, port, SQLite mixin and Postgres
  methods. Kept canonical priority, shared sorting and all schema/migrations.
- Moved the two legacy test adapters to tests/support. Removed the production
  run-construction helper, inlining its sole test use. Kept legacy import parity.
- Removed obsolete feature tests and retained additive schema coverage with a
  direct table check. Initial focused run found two obsolete CLI mock references;
  removed the unused-failure test and updated the no-builder scan check.
- Final focused regression: 250 passed, 17 deselected. Project collection:
  2626 selected / 3103 total, 477 deselected, no collection errors. Scoped Ruff
  F/I checks and git diff --check passed. PostgreSQL integration was not run.
- No live DB changes, push or merge. Earlier full-suite failures remain outside
  this cleanup; the unrelated legacy parity count mismatch remains unfixed.

## Authorized source release completed — 2026-09-28

- Source UI/integration: 5475ec5.
- Partial source persistence and retry state: 29cd8b9.
- CI type, architecture and contract fixes: 61a9295.
- Deferred page-model initialization: c7c4388.
- Final independent snapshot: 2250 backend tests passed, 418 deselected;
  frontend 169 passed, build passed, Ruff/format/mypy passed.
- Regression evidence: partial-source parallel finalization failed with one
  saved job before repair and passed with both jobs afterwards. The full backend
  suite passed with Codex removed from PATH after deferred initialization.
- Push CI 36378811490 and PR CI 36378952175 both passed quality and browser jobs.
- PR #25 rebase merged at 2026-09-28T04:46:56Z. Remote main is
  a423f4f688ce6b11a3033f830cf428c626fec80a; its tree matches the verified branch tip.
- Uncommitted canonical workflow, model changes and other experiments remain
  local. No live database migration, application restart or deployment performed.

## Migration source preservation — 2026-09-28

The remaining authorized source has now been saved in local functional commits.
See [the current migration commit record](migration-commit-record-20260928.md)
for current checks, unresolved failures and exact exclusions. The earlier
runtime model hash hold is superseded by explicit user approval for the existing
implementation only; hash-bearing offline experiments and their dependencies
remain local by explicit user choice. No push, merge or mini deployment occurred.
