# Restore remote two-stage evaluation, retain collectors

Approved scope: remote main two-stage A/B workflow, prompts, defaults, evaluation
UI/API/results sorting. Keep all current collectors, GitHub incremental reuse,
four-tab pacing, LinkedIn frame retry and partial-result persistence. Preserve
unrelated experiments/models and all job/application/user-decision data.

Baseline: origin/main verified equal to local HEAD on 2026-09-17.
Backup: /Users/wenqiwang/wwq/jobfeed-rollback-l6LQQn/workspace.tar.gz and
jobfeed-before.sqlite. No active pipeline runs when service stopped.

Tasks and acceptance:
- [x] Back up complete workspace (excluding dependencies/data) and SQLite.
- [x] Restore remote evaluation behavior, keeping shared collector integrations.
  Evidence: failing then passing remote evaluation/UI tests, collectors regression.
- [x] Archive the exact 603 Evidence Fit evaluation rows and reset their A/B
  fields to pending; preserve all other rows and job/application data.
  Evidence: rehearsal on backup copy, direct SQLite before/after comparisons.
- [x] Rebuild OpenAPI/frontend and restart local service. Real browser check and
  screenshot for user review; no paid evaluation calls, push or merge.

Compatibility boundary: existing additive database tables/columns and standalone
experimental modules may remain inert; no priority projection is invoked by
scanning, evaluation or Results. Avoid destructive database schema downgrade.
Personal provider credentials, resume, source configuration and unrelated ML
experiments stay untouched; runtime evaluation model defaults follow remote.

## Completed evidence — 2026-09-17

- Test-first: restored remote evaluation tests initially failed (3 failures),
  then passed after restoring the implementation. Focused backend/collector/API
  regression: 170 passed, 1 deselected. Extension: 28 passed. Frontend: 160 passed.
- Broad backend regression: 2,270 passed, 342 deselected after excluding existing
  failing tests/modules. Five existing failures were reproduced against the
  pre-change workspace: packaged ML model metadata, code hygiene, existing gate
  file size, and two semantic-gold fixture/data checks. These are outside scope;
  the excluded modules also contain passing tests. This is not a fully green
  unfiltered suite.
- OpenAPI/types regenerated; frontend production build passed. Existing bundle
  size warning remains. `git diff --check` passed.
- Rehearsal and live migration both archived/reset exactly 603 rows. Archive:
  `/Users/wenqiwang/wwq/jobfeed-rollback-l6LQQn/evidence-fit-archive.sqlite`.
  Direct SQLite comparisons found no other evaluation changes, no job/JD or
  status changes, no application queue/applied/history changes, no run history
  or LLM usage changes. SQLite quick check returned `ok`.
- Extension directory and scan/source implementations directly compared equal
  to the pre-change backup. No collector rollback or extension reload was needed.
- Local service restarted on port 7654. Real browser verified full A+B, A-only,
  B-only choices, latest-scan/backlog scope, Results (66 postings), and legacy
  Abridge quick score/detailed score/strengths/gaps/resume guidance.
  Screenshots shown for user review; visual approval before merge is still pending.
- Runtime Stage A uses gpt-5.6-luna; Stage B uses gpt-5.6-sol. Threshold remains
  70. No evaluation was started. Credentials/source configuration were retained.
- Existing compatibility storage and experimental modules remain inactive in
  the restored evaluation workflow; no destructive schema downgrade performed.

No commit, push, merge, or new model evaluation was performed. No release authorized.
