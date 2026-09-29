# Canonical backlog bounded evaluation

## Scope and dependencies

User authorized a delegated fix, Air validation, push to main after passing checks,
then Mini pull/reload. No new schema, hash logic, scheduling, or identity-review
changes. Unrelated Air development setup and offline experiments stay untouched.
Existing source writes already invalidate changed canonical evaluation inputs;
existing canonical SQL already excludes completed results with matching policy.
A second evaluated flag would duplicate that state.

## Implementation

- [x] Bound canonical discovery (including explicit IDs) to at most 100 IDs per
  page, independently of the configured million-candidate scan window.
- [x] Process each claimed batch through filtering and scoring before discovering
  the next batch, for both Stage A and Stage B. Keep the requested limit across
  batches and stop discovery when budget is exhausted.
- [x] Preserve descending candidate order in SQLite claims and reuse one connection
  for the batch, while keeping each parent's input check and fenced claim in its
  own short transaction. Release writer locks between parents.
- [x] Emit discovery/claim progress every bounded page, maintain cumulative gate
  and scoring counters, and keep the displayed phase at Stage A after scoring
  starts so the existing UI does not hide completed work during later batches.
- [x] Preserve source selection, input/policy invalidation, conflicts, stale-claim
  checks, duplicate exclusion, retry limits, and revision/generation fences.
  Selected-source reconstruction is still required by the existing model and is
  now bounded; this change does not claim to materialize all eligibility fields.
- [x] Root review, Air full-data-copy benchmark and full quality checks.
- [ ] Authorized shipping and Mini inactive-run reload verification.

## Verification evidence

Regression initially failed because the old implementation requested 1,000,000
IDs instead of a bounded page. A second regression run against the original
SQLite claim implementation failed because requested newest-first IDs [3,2,1]
were reordered [1,2,3]. Updated tests require one SQLite connection per claim
batch and no duplicate claims, and verify that the first batch is scored before
the next page is fetched for both stages. Existing integration tests exercise
completed-result skipping, changed input/policy reevaluation, conflict holds,
and fenced stale writes.

Commands:

```
.venv/bin/pytest -q tests/integration/test_canonical_evaluate_service.py tests/integration/test_real_job_evaluation_sqlite.py
.venv/bin/ruff check src/jobfeed/services/_evaluate_canonical.py src/jobfeed/adapters/store/_sqlite_real_job_evaluation.py tests/integration/test_canonical_evaluate_service.py
.venv/bin/mypy src/jobfeed/services/_evaluate_canonical.py src/jobfeed/adapters/store/_sqlite_real_job_evaluation.py
```

Benchmark helper `/tmp/jobfeed_backlog_benchmark.py` accepts only a path beneath
`artifacts/canonical-backlog-performance`. It runs the real canonical pipeline
with fake paid calls against a disposable database copy, logs page sizes, time
to candidate progress, time to first score, and completed count. No network LLM
calls or production DB changes are needed.

## Risks and dispositions

- Gates formerly ran over the entire requested set before scoring. They now run
  per batch; existing gate decisions are per job, including deterministic audit
  sampling, so eligibility is preserved. Totals grow as discovery proceeds.
- Stage B begins after Stage A finishes, preserving stage ordering; Stage B itself
  streams bounded batches.
- This is not a full eligibility-materialization migration. Broad backlog SQL can
  still return unclaimable parents, but preparation releases control and updates
  progress between bounded pages instead of waiting on the entire historical set.
- Review found the old UI inferred global completion from stage order. Added a
  cumulative counters while preserving the existing five progress bars and layout.
  User rejected replacing bars with spinners; that presentation was reverted.
  Percentages describe only discovered work and an explicit note says totals may
  grow. Preparation and local gates remain in progress on the rail until A
  discovery is finished. Scan display unchanged.
  Root must obtain screenshot approval before merge.
- Cancellation benchmark exposed an asynchronous-generator cleanup race at store
  shutdown. Regression reproduced Stage B active-connection failure; both stage
  iterators are now deterministically closed with aclosing before returning.

Frontend verification: new multi-batch regression failed on premature 100% before
fix; 16 LiveRunRow tests now pass. TypeScript, ESLint, production Vite build passed;
tracked SPA artifacts rebuilt. Existing large-chunk Vite warning is informational.

## Final Air verification (2026-09-28)

- Frozen 148,029-source-row copy: time to first scoring call improved from
  95.292 s (137,653 IDs discovered first) to 0.997 s (page bounded to 100).
  Calls were mocked; this measures preparation, not LLM throughput.
- Final isolated committed-tree quality: 2,614 passed, 18 skipped, 477
  deselected, 8 expected failures; Ruff/format/mypy passed.
- Frontend: 187 passed; lint, TypeScript and production build passed.
- Restored-bars browser run e328c1ef-8308-41c5-b8ff-746b56bbc913:
  120 Stage A + 120 Stage B, zero errors, mock models on an Air-only copy.
  Observed cumulative candidates grow 59 to 113 while scoring progressed;
  Stage A discovery completion then marked preparation complete at 120.
- Independent final read-only review found no release blocker.
- Screenshot saved locally as artifacts/canonical-backlog-performance/
  air-progress-restored.png; user confirmation is pending before merge.
