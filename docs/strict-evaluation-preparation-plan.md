# Strict evaluation phases and SQL-first preparation

User correction: preparation must finish before all SDE filtering, then all
seniority filtering, then Stage A, then Stage B. A model/prompt/gate rule change
is not authorization to repeat completed evaluations. No new repost classifier,
no new hash, no deployment in this task.

Implementation in progress:
- Restore global phase barriers; keep 100-ID preparation pages and progress updates.
- SQL prefilters canonical status, closure, any complete source, any non-repost
  source and conservative date eligibility. Exact source selection/input checks
  remain atomic. Existing repost semantics preserved; source selection records
  are not described as user actions.
- SQLite reads parent/evaluation/source/selection records in four bulk queries
  inside each bounded writer transaction rather than one query/transaction per
  parent. Preserve revision/generation fencing and cancellation release.
- Remove policy-triggered paid requeue and score masking in SQLite/PostgreSQL,
  preview, detail/list readers and pending counts. Real input changes still
  invalidate; no explicit re-evaluation feature added.
- Restore original five bars and strict-stage completion semantics.

Red evidence: strict preparation-before-scoring regression failed for both A/B
on streaming code. New SQL max_days prefilter test initially failed for absent
parameter. Policy visibility regression failed (old policy hid completed B).
Current targeted Python set: 51 passed before remaining quality/UI verification.

Pending: SQL edge/date tests, batch read-count regression, strict all-gates order,
Air disposable full-data-copy total preparation timing, frontend checks/build,
parent review. Do not treat time-to-first-score as preparation-complete evidence.


## Verification update

- Batch-read regression on old adapter failed: 3 source SELECTs for 3 parents;
  fixed adapter uses 1 source SELECT for the page. Strict-phase regression uses
  candidate pages of size 1 and ML subbatches of size 1: all SDE decisions finish
  before seniority; seniority completes before concurrent LLM calls.
- 59 targeted Python tests passed, including cancellation releasing claims before
  SQLite shutdown, input revision/fence checks, legacy reuse and policy stability.
- Full frontend: 186 passed; TypeScript, ESLint and Vite production build passed.
  Original five bars and known stage totals restored. Tracked SPA rebuilt.
- Ruff on all changed Python files passed; mypy on four changed store/service
  entrypoints passed. PostgreSQL test invocation initially failed because its
  Alembic subprocess was absent from PATH; rerunning with .venv/bin on PATH.
- Air benchmark used a new SQLite backup of mini-policy-rehearsal.sqlite; original
  snapshot and Mini were not modified. For 300 candidates, max_days=30, full
  preparation completed in 1.014 seconds after inspecting 500 SQL-filtered IDs.
  LLM calls before all preparation completed: zero. All 300 mock calls then
  finished normally (3.365 seconds overall), with clean SQLite shutdown.
- Unbounded SQL-only enumeration returned 106739 eligible coarse candidates in
  6.009 seconds with no age filter; max_days=30 returned 36188 in 1.668 seconds.
  This is only SQL enumeration; final exact eligibility still runs in bounded
  transactions. Results do not claim all 106739 were fully prepared.
- Existing one-hour claim TTL and global run lease remain unchanged. The measured
  preparation is far below TTL; paid workers refresh fenced claims before calls.
  This task does not add long-run lease infrastructure or alter repost detection.

Evidence: /tmp/strict-final-focused.log, /tmp/strict-ui-tests.log,
/tmp/strict-preparation-benchmark2.log, /tmp/strict-batch-red.log.

## Full-suite regression follow-up

Root's full suite found the prior per-parent query/transaction test hooks no
longer intercepted the bulk path. Updated them to exercise the actual bounded
page and selection paths, retaining the safety assertions:

- A direct 201-ID claim call performs pages [100,100,1], four bulk reads per page;
  a competing connection successfully writes between every pair of pages.
- RuntimeError and CancelledError during source selection are each tested for
  Stage A/B, inside the first page and after a committed earlier page. Previously
  returned claims remain owned; all unreturned work is rolled back or released
  and can be claimed again. Store shutdown succeeds.
- The 100-parent transaction bound is an explicit adapter constant. Both public
  claim docstrings document the nested-loop complexity.

Lifecycle regressions plus code hygiene: 37 passed (existing size warnings only).
Ruff and diff whitespace checks passed. Root owns final full-suite rerun and
browser acceptance; no deploy was performed by this agent.

## Root final verification

- Isolated tracked-tree make quality: 2,618 passed, 18 skipped, 477
  deselected, 8 expected failures; Ruff/format/mypy passed.
- Real PostgreSQL focused lane: 18 passed; frontend 186 passed, type/lint/build passed.
- Browser run 7d8f847b-bef3-4bef-976e-6debbfc0ac4f on Air disposable DB:
  120 Stage A and 120 Stage B, zero errors with mock models. During Stage A,
  preparation/SDE/seniority showed completed120/120; Stage B waited.
- Screenshot: artifacts/canonical-backlog-performance/air-strict-stages.png.
- Independent final read-only review found no new blocker.
- Changes remain local and uncommitted; no Mini deployment this task.
- Separate 403 investigation: artifacts/codex-403-investigation/20260929-run4716-report.md.
  Exit-zero intermediate reconnect events are wrongly treated as terminal errors;
  local synthetic reproduction and one successful Mini serial probe recorded.
  The adapter remains unchanged because this part was investigation-only.
