# Preserve completed evaluations across scans

## Approved behavior

A scan does not authorize paid re-evaluation. Keep completed canonical Stage A/B
results and their original evaluation input snapshot when source dates, text,
location, or preferred source change. Retain first-known non-null GitHub listing
posting date on source upsert, so relative ages do not move dates every three hours.
Pending/in-progress inputs retain revision fences; explicit result reset is outside
this change. No schema migration, hash logic, bulk score reset, or model-policy change.

## Root cause

GitHub age parsing uses scan time minus N days. Exact input comparison included
posted_at; source sync archived completed scores and cleared their statuses.
At 09:00, 506 of 512 completed input invalidations differed only by posting date.
494 of the 532 Stage A jobs had also been evaluated at 06:00. Scheduler paused.

## Implementation and evidence

- Completed guard precedes input-change reset in both SQLite and PostgreSQL claims.
- Source sync preserves completed result/input snapshots in both stores.
- SQLite reconciliation preview reports retained completed scores accurately.
- GitHub upsert retains an existing posting date; other sources keep their date rules.
- Regression failed before changes: changed JD allowed a second Stage A claim.
- SQLite/selection/date tests: 30 passed. PostgreSQL integration: 18 passed.
- Legacy reconciliation/setup checks: 13 passed.
- Air replay uses an online Mini database backup, no LLM client or production writes.
  First pass processed 532 sources: zero A claims, with two existing identity merges
  recorded as identity_merge, not input_changed. Repeat pass processed 531 sources:
  28,944 completed rows unchanged, zero claims, zero new history; quick_check=ok.
- Full exported-tree quality verification and remote CI are release gates.

## Deployment acceptance

After gates pass: pull main on idle Mini, reload server, scan GitHub through the
real extension, and compare completed scoring records and invalidation history.
Restore the existing three-hour LaunchAgent only after no old completed job is
requeued. Preserve original scores and all unrelated local files.
