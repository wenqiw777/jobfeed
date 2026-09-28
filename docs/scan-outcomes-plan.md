# Scan outcome repair

Approved scope: distinguish exhaustion, uncertain pagination, item enrichment
failures, blocked sources and infrastructure failures. Preserve partial work,
bounded recovery, per-source attribution, structured enrichment error codes,
and visible warnings. No automatic rescan, restart or release.

Acceptance: regression tests first, extension and Python tests, UI tests and
browser verification. No new hash mechanisms. Existing edits are preserved.

## Implemented

- Semantic LinkedIn empty responses get three bounded attempts, then an explicit
  uncertain-page warning. Structured paging totals can confirm exhaustion.
  Duplicate pages advance, with a three-page stagnation guard.
- Completion warnings survive extension bridge, Redis result journals, adapter
  mapping and SQLite run progress. Only genuine errors increment run.errors.
- SpeedyApply partial JD results are warnings when some browser work succeeded;
  all-target failure and bridge/infrastructure failure remain errors.
- Extension emits item error codes, checks redirected-domain permission, and
  performs bounded script reinjection/load recovery. Added the six specific
  missing host permissions observed in the latest scan; version 0.8.2.
- Permission errors cannot be confused with numeric URL substrings. No timed
  native retry for permission blocks: subsequent scans perform local extension
  permission preflight before opening any page.
- Jobright preserves mapped partial jobs, advances duplicate pages, warns on
  repeated pagination and retries transient frame removal at the same cursor.
- Run failure aggregation identifies actual failed sources, including multiple
  concurrent failures. Non-Redis scans use the same error terminal policy.
- Runs history/live rows show source outcomes/messages and completion warnings.

## Evidence and limits

- Red: URL permission classified transient, structured error argument absent,
  empty semantic page threw immediately, missing typed extension permission code,
  UI omitted warning message, repaired permission still deferred for seven days.
- Green: 53 Python tests plus two additional classification/attribution checks;
  42 extension tests; 10 LiveRunRow UI tests; TypeScript, targeted mypy/Ruff,
  diff whitespace and isolated production UI build passed.
- Real temporary SQLite readback verifies warning persistence; live Redis tests
  verify pending work remains a failure. Chrome extension browser inspected the
  dev Runs page and captured the expanded four-source history screenshot.
- No live source scan was triggered and historical failed runs were not rewritten.
  Semantic LinkedIn end-of-results markup is not assumed; without a proven end
  signal it intentionally remains a warning, not a false exhaustion claim.
- Existing per-item transient/parse cooldowns remain; no new background retry
  scheduler or login-recovery UI was introduced.
- Activation pending extension reload, backend restart and serving the rebuilt
  UI. No commit, merge or deployment performed; UI screenshot approval pending.
