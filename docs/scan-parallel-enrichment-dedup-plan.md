# Scan All parallelism, incremental enrichment, and exact dedup

Status: full-size scan exposed a checkpoint writer storm; repair implemented and full-size verification running. No shipping authorized.

## Full-size checkpoint repair (2026-09-18 evening)

- Full runs c446dcfe-b619-4779-9cde-afd920e76e70 and
  8b6c6e30-25fe-4298-94db-c300be43481c failed with database is locked.
  The five-row acceptance did not cover this load.
- Diagnosis: every scan progress callback spawned a checkpoint task, and fetch
  progress called the callback twice. Observed 2,086 database handles in the API
  process and 3,098 checkpoint-failure log entries across the failing runs.
- Red test: 5,000 progress events created 5,000 pending writers (expected one).
- Repair: retain immediate SSE updates, coalesce durable scan progress into one
  pending writer per run with a one-second window, flush immediately on drain,
  and retain fenced terminal persistence. Removed duplicate progress callback.
  Evaluation behavior is unchanged. Old idle failed worker exited gracefully.
- Green: 45 lifecycle/scan/lease regressions; separate real SQLite four-lane
  4,000-event integration test persists final progress and clears pending state.
  Focused Ruff and mypy pass.
- Full-size run 7cb5dc24-1f0c-4dc9-a0c9-377c11db605f started with original
  limits, passed the earlier failure point, saved all 605 Handshake rows and
  continued the other three sources. Sampled database handles: 12, not thousands.
  Terminal result remains pending; do not claim full-size acceptance yet.

## Execution evidence (2026-09-18)

- Identity: red import/behavior tests, then 13 passing extraction/clustering checks.
- Persistence: red missing-field contract, then passing SQLite retry/reuse contracts;
  Alembic 0013 and real Postgres identity/retry/progress round-trip pass.
- Real SQLite copy migrated successfully; quick_check=ok; 99,920 URLs recognized.
- Legacy alias resolution follows an aggregator URL's native ID to the same
  platform's canonical ID and official URL. HRT 8052122 resolves to an existing
  FULL 2,152-character JD through Jobright ID 6a5505cf377f983ce8a973cf.
- Four-lane backend test initially timed out under the global lock, then passed;
  service-worker entry test accepts four lanes and rejects an occupied lane.
- Interleaved progress regression failed before the per-source map, then passed.
- Full fake-bridge ScanService integration saves four sources and hydrates all
  four terminal progress rows from SQLite.
- Relevant regression batches: 242 Python checks passed; later targeted batch
  158 passed. UI: 160 checks passed before the new lane case; current lane UI
  suite has 9 passing checks. Focused mypy passes; repository mypy is blocked by
  pre-existing scipy stub and jobboard_extension annotation errors.
- User approved preserving LinkedIn=1000 and Handshake=700 and raising their
  validation ceiling to 1000. Boundary tests fail first, then 3 pass.
- Local API restored, health reports db=ok. Extension connected; manual Reload
  requested because browser tooling forbids chrome://extensions/ access.
- Final focused regression: 54 Python checks and all 31 extension checks passed.
  Production UI build and TypeScript compilation passed.
  All 161 UI checks passed with two test workers (initial unrestricted run had
  two failures under load). Postgres retry/progress and online alias resolution:
  2 passed; the alias test failed before correcting its returned official ID.
- Actual HRT copy replay: FULL 2,152-character JD reused with zero native calls
  and zero browser calls. Jobright now checks stored complete JDs before its
  official enrichment route as well.
- Browser screenshot of an isolated fake-source run shows all four independent
  progress bars at 8/60, including SpeedyApply browser enrichment. This verifies
  UI rendering/streaming, not the loaded Chrome extension or real source sites.
- Broader Postgres checks: 126 passed, one unrelated existing posted-date sort
  expectation failed (query uses COALESCE(posted_at, discovered_at)). Left intact.
- Pending: loaded-extension live four-lane run, unchanged-list second scan,
  and user UI screenshot approval before any merge. Browser policy forbids
  opening extension management; user must Reload the installed extension.
  No commit/merge/release authorized or performed.

## Live acceptance (2026-09-18 evening)

- User reloaded the extension. Existing API returned HTTP 500; no active database
  leases were present. Restart restored health and extension connectivity.
- First restart accidentally omitted the explicit config path and used defaults.
  Its ATS-only run f3b29342-21e8-4db0-aa34-6d8c6d8ea5d2 was immediately stopped
  with zero discovered jobs. Restarted with the repository config explicitly.
- Bounded overrides used max_jobs=5 without editing config.toml. LinkedIn's
  existing per-query behavior returned 8 unique jobs from two queries.
- Real run 5f7bf350-4c27-4b20-8c31-329c3bc8d262 completed in 40 seconds:
  Handshake 5, Jobright 5, LinkedIn 8, SpeedyApply 5. Handshake completed while
  SpeedyApply browser enrichment, Jobright and LinkedIn were still active.
  Browser screenshot confirms four independent source rows.
- SpeedyApply first pass: 3 reused, 1 browser enriched, 1 missing; missing row
  sa-9cd23670c8ff452d persisted no_complete_jd with retry_after September 26.
- Repeat run 0d336da3-26b4-452b-9884-2bec2bb5e126: SpeedyApply completed in about
  one second with 4 reused, 1 retry_deferred, 0 native_enriched, 0 browser_enriched.
  Existing incomplete JD remains incomplete rather than being reported enriched.
- Repeat run finished with 23 existing rows updated, zero inserted and zero run
  errors. Direct database quick_check=ok. Original limits restored afterwards.
- Scope of proof is the bounded sample, not a full 1000/700 scan; prior HRT
  database replay supplies the specific HRT reuse evidence. Merge remains gated
  on user approval, including the UI screenshot.

## Outcome

`Scan All` runs its four enabled sources as four independently progressing lanes:

1. Jobright
2. LinkedIn Extension
3. Handshake
4. SpeedyApply

One lane must not wait for another lane's Chrome task. SpeedyApply refreshes its
configured lists every run, but enriches only genuinely new or still-eligible
unresolved jobs. Existing complete JDs are reused across sources when the jobs
share an exact external identity. Duplicate source rows remain auditable in the
database, while enrichment, evaluation, and display operate on one exact job
identity where available.

No hash field, hash computation, hash validation, or new hash dependency is in
scope.

## Scope

### Included

- Replace the bridge-wide Chrome task lock with four isolated source lanes.
- Permit one active extension task per lane, with task-owned tabs, cancellation,
  result routing, and progress.
- Keep the existing four-tab pool inside the SpeedyApply browser-enrichment lane.
- Extract stable external identities from supported URLs and source payloads.
- Reuse nonblank `GOOD`/`FULL` JDs across sources before any network enrichment.
- Avoid retrying deterministic enrichment failures on every scan.
- Deduplicate browser targets and evaluation candidates by exact external
  identity.
- Expose independent per-source progress and SpeedyApply enrichment subphases in
  the Runs API/UI.
- Support SQLite and Postgres with matching behavior.

### Deliberately excluded

- Deleting or merging persisted source rows.
- Copying a JD based only on similar company/title text.
- Semantic or embedding-based job identity.
- Changing the scoring, eligibility, or application-status rules.
- Refreshing an existing `GOOD`/`FULL` JD merely because it is old.
- Running two tasks from the same source lane simultaneously.
- Changing source rate limits or bypassing site access controls.
- Starting, stopping, or restarting the currently active scan.
- Shipping, committing, merging, or releasing without separate authorization.

## Current behavior

### Scan concurrency

- `ScanService` starts all sources with `asyncio.gather`.
- Every extension-backed operation then enters one global
  `JobrightBridge._scan_lock`.
- The extension rejects every new task while `activeTasks.size > 0`.
- Jobright, LinkedIn, Handshake, and SpeedyApply browser enrichment therefore
  serialize behind one Chrome task even though the source coroutines were
  launched concurrently.
- SpeedyApply browser enrichment can own four tabs internally, but the other
  three sources wait until the entire enrichment task releases the bridge.
- Live progress is represented by one mutable `scan_source`/`scan_phase` pair;
  concurrent updates overwrite one another. SpeedyApply's browser progress is
  logged but not surfaced to the run.

### SpeedyApply enrichment

- Every scan refreshes all configured Markdown/HTML lists.
- A complete persisted JD is reused only for the exact pair
  `(platform="speedyapply", canonical_id)`.
- The SpeedyApply canonical ID is derived from the full apply URL. Different URL
  forms or tracking parameters can therefore represent the same real posting as
  different stored rows.
- Cross-source complete JDs are not consulted.
- Rows still marked `MISSING`, `STUB`, or `PARTIAL` retry native routing and then
  Chrome enrichment on each scan, except definitively closed rows.
- A browser page opening means only that enrichment was attempted. It does not
  mean a JD was successfully persisted.
- A deterministic failure such as `Job identity not found in page` has no retry
  cooldown, so the same page can reopen the next day or later in the same day.

### Deduplication

- The database intentionally keeps every `(platform, canonical_id)` source row.
- Evaluation/display soft-cluster rows by normalized `(company, title)` and pick
  a representative by quality, source priority, posting date, then stable ID.
- The soft key can group different requisitions with the same title and cannot
  reliably connect the same requisition when titles differ.
- Soft clustering happens downstream and is not used for enrichment reuse.

## Required behavior after the change

### 1. Four independent lanes

The extension and backend expose these production lanes:

| Lane | Commands | Maximum active tasks | Tabs |
| --- | --- | ---: | ---: |
| `jobright` | Jobright recommendations | 1 | 1 |
| `linkedin` | LinkedIn signed-in scans/search URLs | 1 | 1 |
| `handshake` | Handshake signed-in scan | 1 | 1 |
| `enrichment` | `github-jd` rendered fallback | 1 | 4 |

- Different lanes may run simultaneously.
- A second task in the same lane receives a source-specific busy error.
- `tiktok` continues to share the enrichment lane unless separately redesigned.
- Pilot/manual scans remain globally exclusive so they cannot interfere with a
  production scan.
- Cancellation or failure closes only the tabs owned by that task.
- One lane's error increments that source's error count but does not cancel the
  other lanes.
- The backend continues using one WebSocket. `task_id` is the routing key for
  commands, batches, progress, completion, errors, and cancellation.

### 2. Database-first enrichment

For every discovered posting, execute the following decision ladder before any
native HTTP or browser enrichment:

1. Refresh listing metadata from the upstream source.
2. Extract an exact external identity when supported.
3. Reuse the current row's existing nonblank `GOOD`/`FULL` JD when present.
4. Otherwise look across all platforms for a nonblank `GOOD`/`FULL` JD with the
   same exact external identity.
5. Otherwise, if the same unresolved identity has a future `retry_after`, keep
   the existing incomplete state and do not make a network request.
6. Otherwise run the existing native/official HTTP route.
7. Only if it remains unresolved, enqueue one browser target for that exact
   identity.
8. Promote to `GOOD`/`FULL` only after identity validation and quality
   assessment pass. A page open or a nonempty summary is not success.

Reuse copies only JD/enrichment provenance. The newly discovered row retains its
own source, canonical ID, URL, title, company, location, and posting time.
`enrich_source` records the origin in an auditable form such as
`reused:linkedin-extension:<original-source>` while retaining the original
enrichment timestamp.

### 3. External identity contract

Add one nullable, non-hash `external_identity` value to a job. It is a namespaced
vendor-native identity, not a generated digest. Initial supported identities:

- `greenhouse:<job-id>` from direct Greenhouse URLs, embed `token`, and custom
  company URLs containing `gh_jid`.
- `linkedin:<job-id>` from `/jobs/view/<id>`.
- `ashby:<uuid>` from Ashby posting URLs.
- `lever:<uuid>` from Lever posting URLs.
- `workday:<tenant>:<requisition-or-job-id>` when the URL exposes both scope and
  ID.
- `jobright:<job-id>` only when no stronger official identity is present.
- `handshake:<job-id>` only when no stronger official identity is present.

Known tracking parameters are ignored while extracting identity, but
job-defining parameters such as `gh_jid`, Greenhouse `token`, tenant, and
requisition ID are preserved. Unsupported URLs receive `NULL`; they never fall
back to fuzzy identity for JD reuse.

When a source payload provides both an aggregator ID and an official apply URL,
the official vendor identity wins. Example: HRT URLs containing
`gh_jid=8052122` and Greenhouse embed URLs with `token=8052122` both resolve to
`greenhouse:8052122`.

### 4. Retry behavior

Persist explicit enrichment-attempt state:

- `enrich_attempted_at`
- `enrich_error_code`
- `enrich_retry_after`
- existing `enrich_error` remains the human-readable detail

Policy:

- HTTP/network/5xx/429/timeout: retry after 6 hours.
- `identity_not_found` or `no_complete_jd`: retry after 7 days.
- missing extension permission: retry after 7 days or immediately after a
  newly supported identity/permission is detected.
- 404/410/definitively unavailable: use the existing `closed_at` terminal path.
- A changed external identity or a newly available exact `GOOD`/`FULL` twin
  bypasses the cooldown immediately.

The retry decision is made before opening a tab. A failed attempt is visible as
failed, not as enriched.

### 5. Deduplication contract

- Persist all source rows; no destructive deduplication.
- Exact `external_identity` is the first and strongest cluster key.
- One exact-identity cluster produces at most one native/browser enrichment
  target and one evaluation representative.
- A successful JD may populate incomplete members of the same exact cluster.
- If no external identity exists, retain the current normalized
  `(company, title)` soft cluster for display/evaluation only.
- Never copy a JD using the soft key.
- Existing status-aware representative selection remains unchanged: an applied
  or interviewing twin may remain the displayed representative.
- Conflicting exact identities are never merged even when company/title match.

### 6. Progress contract

Replace the single live source fields as the authoritative progress model with a
per-source progress map persisted and returned by the Runs API. Each source has:

- `phase`: `queued`, `fetching`, `native_enrichment`, `browser_enrichment`,
  `saving`, `completed`, or `failed`
- `processed`
- `total`
- `current_job_id` when available
- `updated_at`
- completed counters including `reused`, `native_enriched`,
  `browser_enriched`, `retry_deferred`, `deduped_targets`, and `failed`

The Runs UI renders four simultaneous source rows. The legacy singular fields
may remain temporarily for compatibility, but they are no longer used to infer
whether another source is blocked.

## Implementation tasks and evidence

### Task 1: Exact identity extraction

Result: a pure URL/payload identity extractor returns the identities defined
above without creating hashes.

Test-first evidence:

- Red contract tests for HRT direct `gh_jid`, Greenhouse embed `token`, direct
  Greenhouse, LinkedIn, Ashby, Lever, scoped Workday, tracking-parameter
  equivalence, unsupported URLs, and job-defining parameter preservation.
- Green tests plus Ruff for the new module/tests.

Decision return: stop if a supported vendor cannot expose a stable native ID
without fetching a page; leave it `NULL` rather than inventing identity.

### Task 2: Persist identity and retry state

Result: SQLite and Postgres store the new non-hash identity/retry fields with
parity, indexes, migrations, import/export support, and round-trip contracts.

Test-first evidence:

- Schema/manifest tests fail before the migration and pass afterward.
- Store contracts prove lookup of the best `GOOD`/`FULL` enrichment by exact
  identity and prove `NULL` identities do not match one another.
- Migration applies to a temporary copy and both database quick/parity checks
  pass.

Decision return: migration numbering must be resolved against the current dirty
checkout's migration head before editing.

### Task 3: Database-first reuse and bounded retry

Result: SpeedyApply skips all network work for same-row or exact cross-source
complete JDs and defers ineligible failures.

Test-first evidence:

- HRT fixture: direct `gh_jid=8052122`, Greenhouse embed `token=8052122`, and a
  previously complete twin produce zero native and zero browser calls.
- Different Greenhouse IDs with the same company/title never reuse a JD.
- Recent deterministic failure opens no tab; expired retry becomes eligible.
- Changed exact identity bypasses old retry state.
- Stats distinguish reused, deferred, native, browser, and failed outcomes.

### Task 4: Exact target deduplication

Result: one browser target is emitted per external identity and a successful
result is applied to every incomplete member of that exact cluster.

Test-first evidence:

- Multiple URL forms for one Greenhouse ID emit one target.
- Different IDs with identical titles emit separate targets.
- A result whose returned identity does not match is rejected.

### Task 5: Backend lane concurrency

Result: `JobrightBridge` serializes per lane, not globally, and routes concurrent
task messages by `task_id`.

Test-first evidence:

- Four tasks enter their four lanes before any is released.
- A second same-lane task waits or receives the defined busy result.
- Out-of-order batches/completions reach the correct callers.
- Cancelling one task leaves the other three alive.
- Disconnect fails all pending callers with each caller's own partial rows.

### Task 6: Extension lane concurrency

Result: the service worker accepts one task per lane, owns resources per task,
and retains the enrichment lane's four-tab pool.

Test-first evidence:

- Jobright, LinkedIn, Handshake, and enrichment tasks coexist.
- Total expected production tabs are 1 + 1 + 1 + 4.
- Same-lane duplicate is rejected without affecting other tasks.
- Completion/error/cancel closes only that task's tabs.
- Socket reconnect cleans every active task.
- Existing extension test suite and JavaScript syntax checks pass.

### Task 7: Per-source progress

Result: run state, persistence, OpenAPI, generated TypeScript, and Runs UI expose
the four independent progress rows and SpeedyApply subphases.

Test-first evidence:

- Interleaved updates preserve all four sources rather than overwriting one.
- Restart/recovery hydrates the same progress map.
- Web tests render simultaneous progress and completed counters.
- Browser verification captures the active four-row UI before merge approval.

### Task 8: End-to-end verification

Result: a full fake-bridge integration run proves the complete behavior before a
live scan.

Required evidence:

- All four sources start before any source finishes.
- Existing exact-identity JDs cause no network/browser calls.
- Only new/eligible unresolved identities enter browser enrichment.
- Duplicate URL forms produce one enrichment/evaluation target.
- One source failure does not cancel the other three.
- SQLite `PRAGMA quick_check` and Postgres parity checks pass.
- Focused Python, extension, web, typecheck, lint, and build checks pass.

Live acceptance requires reloading the unpacked extension, restarting the local
backend only after the active scan finishes, running one bounded Scan All, and
verifying:

- four source rows advance concurrently;
- no HRT page opens when its exact Greenhouse identity already has a complete JD;
- a second scan over the unchanged lists sends only newly eligible unresolved
  targets to Chrome;
- source counts and saved rows reconcile;
- the user approves the Runs UI screenshot before merge.

## Ordering and boundaries

Task order is 1 -> 2 -> 3 -> 4 -> 5/6 -> 7 -> 8. Tasks 5 and 6 may be developed
independently after the protocol contract is fixed, but the repository is
currently a shared dirty checkout, so implementation remains single-agent and
must preserve all unrelated changes.

The first verifiable result is Task 1's pure identity contract. The implementation
must not edit reload-watched production files while a live scan is active. A
change in extension/browser support that prevents four task-owned lanes from
coexisting invalidates the lane design and requires returning to design rather
than silently restoring global serialization.
