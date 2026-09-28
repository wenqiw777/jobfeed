# Results loading performance

User authorized implementation and a real browser demonstration, September 22.

Acceptance: preserve current Results membership and exact ordering for all four
sorts on a fixed database; reduce repeated read work; verify real API and browser.
No new hash fields, calculations or dependencies. Preserve unrelated edits.

- [x] Record fixed-database baseline and failing behavioral performance checks.
- [x] Optimize list counts and content comparisons without changing semantics.
- [x] Keep expensive folding off the event loop and cancel obsolete client reads.
- [x] Verify parity, targeted tests, measured API and browser behavior in a demo.
- [x] Restart original service with explicit project config and verify there.
- [ ] Diagnose remaining first-request latency (22.275s after restart).

The old priority snapshot is stale and has different eligibility semantics; it
cannot replace current Results without a separate semantic migration. First
measure direct optimizations before adding a persistent projection.

## Implementation and evidence

- Narrow SQL sorting excludes JD bodies; hydrate afterward in the same read
  transaction. Verdict-required count queries use the selective verdict join.
- Normalize each content body lazily once per fold and skip conflicting employers.
  Failing regression checks originally saw 4,950 unnecessary comparisons for 100
  employers and 380 body normalizations for 20 copies; now zero and at most 20.
- Worker-thread fold with four-entry bounded exact-input reuse; no digests.
  Tests verify current scores, changed JD/status/order/new rows, and eviction.
- Forward the query cancellation signal to browser fetch. This does not claim
  server-side cancellation of already running work.
- 49 backend tests pass; 8 existing expected failures. 25 frontend tests pass.
  Targeted Ruff, mypy (four modules), TypeScript and production build pass.
- `artifacts/results-performance/final.json`: all four cold and repeated sorts
  retain exactly the 2,008 IDs and ordering in `before.json`.
- Timing is load-sensitive. Baseline used cProfile and final uses a worker thread,
  so these files must NOT be used to claim a wall-clock speedup percentage.
  Current fixed-database first-sort samples: 4.91–10.71 seconds; repeated samples:
  2.93–8.08 seconds during concurrent local work. Earlier unprofiled samples were
  2.12–3.48 seconds. No claim of subsecond first load.
- Isolated HTTP demo on 127.0.0.1:7660 uses the real jobs routes/service and built UI
  against the fixed database. No scan workers or mutation endpoints; middleware
  blocks writes. A sequential posted-desc API sample returned HTTP 200, total
  2,008 and 50 rows at offsets 0/50/100 in 4.191/0.916/0.877 seconds respectively.
  Offset 0 was not guaranteed cold (browser had already requested this sort).
- Real browser verified visible Results, score ascending (5-point jobs first),
  and pagination. User-facing demo: http://127.0.0.1:7660/triage.

## Open limitations / handoff

Original development server 7654 stopped completing requests during reload/work:
one HTTP read timed out at 60 seconds and another at 15 seconds. Cause not proven;
do not attribute that delay solely to SQLite or claim it fixed. It was not forcibly
restarted because background scan work is active. No push, merge, or cloud deploy.
Existing 10,000-candidate processing cap remains unchanged; this is not a complete
large-scale query/projection redesign. The old snapshot still needs a separate
semantic migration before it can replace exact Results.

## Authorized restart verification (September 22, 03:48 UTC)

User authorized pausing background work and restarting. No scan was running;
evaluation `017a0215-813a-4141-ae21-2c2b3efc1391` was still active. Stop HTTP API
timed out; terminated the verified old worker after graceful signals did not exit,
and used the existing store stop operation. Durable status is `failed/user_stopped`;
completed job data was preserved. No automatic resumption requested or performed.
Old dev supervisor also stopped Vite. Current real app serves built UI at 7654,
without hot reload, explicitly setting JOBFEED_DEV_CONFIG to the project config.
The temporary 7660 demo is stopped.

One startup raced the stop operation's schema initialization and failed with a
database lock; serial startup succeeded. An initial bare uvicorn startup omitted
the project's config and showed 6,302 results; discarded those measurements and
restarted with explicit config. No config file or filtering code was changed.

Correct-config real API: every request HTTP 200, 2,009 results, 50 rows.
Times in seconds, ordered first page / next page / repeat first page:

| Sort | First | Next page | Repeat |
|---|---:|---:|---:|
| First seen descending | 22.275 | 2.466 | 0.466 |
| First seen ascending | 3.862 | 1.771 | 0.783 |
| Fit score descending | 2.683 | 0.604 | 0.497 |
| Fit score ascending | 3.274 | 1.466 | 0.417 |

Only the first row's first request was process-cold. These are samples, not p95 or
a controlled pre/post speedup claim. First-request latency remains unresolved.
Config API 3.4ms. Active-run API returns an empty list and DB running count is zero.
Backend regression rerun: 49 pass, 8 expected failures. Frontend rerun: 25 pass.
Behavior review: filters, scores, representative choice, order and 50-row pagination
unchanged by this performance patch; exact-input cache returns current row data.
Browser request cancellation on obsolete sorts is the intentional interaction
change. No status buttons were clicked during verification.

## September 22: approved metadata-only Results semantics

The user explicitly chose conservative exact evaluation reuse rather than
semantic-similarity merging. Results grouping is now separate from evaluation:
different locations and different requisitions remain independent rows. The
original 2,008-row parity acceptance above describes the earlier optimization,
not this intentionally changed product behavior.

- [x] Test-first: five new Results tests initially failed (body comparison,
  body loading, location collapse, unknown-identity collapse, source preference).
  Results now use trusted external posting identity plus nonempty location,
  normalized only for case/whitespace. Unknown identity/location stays separate.
- [x] LinkedIn is preferred among equivalent workflow states; in-flight and
  shortlisted status precedence remains intact. No title/company-only matching.
- [x] Both list branches and in-flight lookup omit JD bodies. SQLite's existing
  metadata path and a new explicit PostgreSQL metadata projection serve these
  requests. No JD comparison or content cache is used by Results.
- [x] A new integration test first failed because an applied alias in another
  location was returned as a twin. Detail aliases and bulk status cascades now
  use the same trusted identity/nonempty-location boundary in both databases.
  Single-record transition behavior is unchanged. Old fixture expectations now
  supply explicit shared identity rather than relying on company/title.
- [x] Add location to JobSummary and display it below each Results title.
  DTO test initially failed with missing location and browser component test
  initially failed to find Seattle, WA. OpenAPI and TypeScript regenerated.
- [x] Backend focused regression: 41 passed (13 deselected). PostgreSQL alias/
  cascade subset: 11 passed (14 deselected). UI: 25 passed. Targeted Ruff and
  mypy (7 production modules) passed. Production UI build passed; existing large
  bundle warning remains, unrelated to request-path optimization.
- [x] Main-agent additional checks: workflow routes/service/ports 31 passed,
  23 deselected; legacy content-fold tests 25 passed, 8 expected failures.
- [x] Main-agent live API/browser/screenshot acceptance after integration.

Main-agent fixed-database measurement in
`artifacts/results-performance/metadata-only.json`: 2,341 independent rows,
first sort 0.759 seconds and the next seven reads 0.169–0.210 seconds. Previous
content-merging semantics produced 2,008 rows on that snapshot; do not claim
an identical-result-set speedup percentage. Existing 10,000-candidate cap remains.
Conservative grouping cannot connect two sources with unrelated native IDs
unless a trusted shared posting identity is present. Multi-location strings
within one source record are not automatically split into new source records.

## Coordinator integration verification

- Independent review found empty-string external identities were incorrectly
  linked in detail queries (NULL was already guarded). New SQLite regression
  first reported 1 failure / 1 pass; both databases now reject empty identities.
  Independent SQLite + ephemeral PostgreSQL re-review: all 4 cases passed.
- Combined backend integration command: 150 passed, 13 deselected.
- PostgreSQL twin/cascade suite: 11 passed; gate-candidate suite: 21 passed.
- Full frontend parallel run was inadvertently selected by a command separator;
  it reported 20 failures amid heavy test contention. Single-worker rerun reduced
  this to one malformed configuration-test fixture missing required discovered_at.
  The fixture now supplies a valid timestamp and location; no production date
  behavior or test timeout was changed. Full single-worker rerun: 162 passed.
- Read-only snapshot browser verified visible location, ascending Fit score and
  next-page content change (Visa Bellevue posting on page 2). No status actions.
- Actual application restarted with explicit project config, no hot reload,
  on 127.0.0.1:7654. No active runs before or after restart. Temporary demo 7660
  stopped. Existing scores/application data were not re-evaluated or reset.

Live API September 22 04:58 UTC: every request succeeded, 2,342 results and
50 rows with location. Seconds: first page / next page / repeat first page.

| Sort | First | Next | Repeat |
|---|---:|---:|---:|
| First seen descending | 1.609 | 0.452 | 1.064 |
| First seen ascending | 0.446 | 0.178 | 0.209 |
| Fit score descending | 0.158 | 0.172 | 0.204 |
| Fit score ascending | 0.189 | 1.141 | 0.458 |

These are observed samples, not p95 or a controlled speedup percentage against
the earlier 22.275-second sample. Operating-system cache/memory pressure varies;
the removal of JD hydration/comparison is enforced by tests independently of
wall-clock variability. Current live count differs from the fixed snapshot by one.

## Canonical Triage query latency (2026-09-26)

- User report: Triage eventually displays after a long load. Live health took
  0.046 seconds and detail 0.304 seconds, while the list took 21.718 seconds.
  SQL profiling found 16.988 seconds across count, page and tab-count queries.
- SQLite now bounds candidates to evaluated jobs or identity-review jobs before
  expensive source/date reads. Existing exact visibility predicates still apply.
  The response total reuses the tab-count result, removing a redundant count.
  Scope: preserve filters, ordering, counts and scores; no schema changes.
- Recorded failing performance check: 16.988 seconds against a 2-second budget.
  After the change, the same profile passed at 1.301 seconds. Evidence is under
  `artifacts/triage-latency/` (before/after profiles and parity verification).
- One read-only database transaction compared all fields and ordering for all
  2,711 results across four sorts: exact equality. Tab counts and total also
  matched exactly. Live data continues changing, so subsequent live counts vary.
- Focused SQLite workflow, evaluation and HTTP route regressions: 45 passed.
  Targeted Ruff and `git diff --check` passed.
- Live list API measurements: first page 1.574 seconds, next page 0.683 seconds,
  repeated first page 1.192 seconds. These are samples, not a latency guarantee.
- Reloaded the real browser Triage page and inspected its rendered screenshot:
  rows, scores, recommendations and pagination displayed normally. No precise
  browser end-to-end timing was recorded. Change is active locally; no push or
  merge performed.

## Full localhost startup regression (2026-09-26)

- User reported every fresh localhost:5173 visit waits roughly ten seconds.
  Browser CDP reproduced /api/config 0.015s, personal-ml/status 4.678s,
  and two identical canonical list requests, with the completed one taking
  10.734s. The prior isolated config check missed the concurrent startup load.
- PersonalMLLearningService now shares only pending reads for the same threshold
  and enabled state. Cancellation of one reader does not cancel other readers;
  completed and failed reads are discarded so subsequent reads remain fresh.
  Regression first failed with three database reads instead of one, then passed.
- Canonical list queries retain their keyed in-flight fetch across mount/unmount.
  Previously the startup remount aborted HTTP but left SQLite work running, then
  sent another identical list query. Browser now issues one list request.
- Initial browser repeat results were 2.014s and 4.427s for the list, showing
  remaining per-read history cost. Added idx_eval_personal_ml covering completion
  time, job ID and quick score for completed evaluations, with additive existing
  database installation. No scoring logic, labels or data changed.
- Disposable-copy experiment: 31,504 rows exactly equal before/after; 3.791s to
  0.183s. Live transaction: 1.201s to 0.059s with exact ordered equality; query
  plan uses the covering index. Live evidence: artifacts/startup-latency/live-index.json.
- Backend learning/concurrency/schema/lifecycle/index tests: 46 passed.
  Frontend query tests: 5 passed; TypeScript passed; targeted Ruff passed.
  Browser final timing and screenshot acceptance pending below.
- Final repeated browser reads still varied (4.795s then 2.824s), so eliminated
  the remaining jobs table payload reads too: idx_jobs_personal_ml covers only
  ID, gate score, fail reason and role type; the observation query explicitly
  selects that covering index. Copy experiment: 1.997s to 0.025s, equal rows.
  Live 31,504-row parity also passed at 0.015s (live-covering.json).
- The live index was recreated from schema_ddl_statements to match the runtime's
  exact SQL-definition validator after whitespace in hand-written DDL caused a
  startup validation error. Startup was rechecked successfully, and the existing
  dev reloader restarted via touching its factory file. Health recovered; final
  personal-ml/status read took 0.087s. No job data was deleted or changed.
- Final backend suite including route tests: 51 passed. Targeted Ruff and diff
  checks pass; frontend ESLint passed. Final browser evidence follows.
- Final two complete root navigations (fresh React/query state each time):
  config 0.006/0.013s, personal ML 0.110/0.104s, one list request 2.252/2.445s,
  detail 0.090/0.057s. Browser accessibility confirmed Jobs visible and no
  Loading settings; screenshot captured. These are observed samples, not p95.
  Frontend final recheck: 5 passed and TypeScript passed. Active locally, no
  commit/push/merge. The brief bootstrap loading state remains while config
  arrives; it is no longer coupled to redundant startup database work.
