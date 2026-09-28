# Scan failure investigation and repair (2026-09-20)

Authorization: investigate deeply, test and modify local data, repair and verify with real scans. Preserve unrelated edits. No new hash mechanisms, push or merge.

Acceptance: reproduce each confirmed defect with a failing regression; repair narrowly; verify retry preserves persisted jobs and really retries failed work; activate local changes and observe a terminal real scan through DB and browser.

- [x] Capture original run, journal and runtime evidence; back up before data adjustments.
- [x] Distinguish Redis latency, page readiness/navigation, fetch timeout/abort and bridge disconnect.
- [x] Test-first repairs for demonstrated causes and retry defects.
- [x] Targeted tests, local activation, real retry, DB and UI readback.

Observed original retry 3cf5e571-794a-4d17-a8f8-109eb770df83: SpeedyApply Redis read timeout; Jobright load timeout after 140 saved rows; Handshake browser abort after 25; LinkedIn subsequently disconnected after 668. Final run failed with one pending Redis task. User confirms no manual cancellation of these source requests. Error text alone does not establish a manual action or timeout cause.

Hypotheses to test: Redis concurrent journal pressure; page readiness race or navigation abort; failed bridge responses durably cached as completed results, preventing actual retry. Do not infer memory exhaustion from previous incidents.

## Confirmed evidence and repairs

- Backups: `artifacts/scan-timeout-repair/before.sqlite` (SQLite online backup) and `journal-before.jsonl` (6,105 original-root keys, serialized Redis values plus TTLs). No manual deletion or rewriting of historical job rows.
- Baseline real Retry `a6168b6a-bdb5-4a64-b5ae-b9078c8444cf` replayed all three cached browser errors within about 0.2 seconds; SpeedyApply alone resumed real work. Original journal contains error-bearing `bridge:*` AND `source:*` completed results. This confirms the retry defect rather than inferring it from error text.
- The diagnostic retry was deliberately stopped by this agent before local API activation. This new stop is separate from the original user's reported browser abort.
- Redis regression red: mixed source partial failure plus another pending source replays the error forever; lost replies after enqueue/completion abort despite successful Redis writes. Green: fenced retry of completed error results preserves partial jobs; successful/warning results remain reusable. A source attempt generation separates new batch receipts from old partial-write receipts. This also preserves jobs that disappear from subsequent discovery.
- Only idempotent Redis journal operations retry connection/timeouts (three bounded attempts). RPUSH partial appends are explicitly excluded. No eviction, memory-limit, identity or hash changes.
- Browser regression red: a completion event between status read and listener installation is lost; an interactive source document is blocked by incomplete resource loading; temporary aborts and timeouts immediately fail the source. Green: listener-before-read plus polling; source API work waits for a same-origin interactive/complete document; read-only fetch requests retry at the same cursor, at most three attempts, with source/offset/timeout-vs-abort diagnostics. Authentication failures are not retried.
- Tests: 44 targeted Python checks, 47 extension checks, targeted Ruff and mypy (3 modules) pass. SQLite quick_check: ok. Evidence outputs are in `artifacts/scan-timeout-repair/`.

## Runtime / outstanding verification

- Local API restarted with the original process environment; PID 71940, log `artifacts/scan-timeout-repair/api.log`. DB and Redis health are ok.
- Chrome control timed out repeatedly (native accessibility and extension browser navigation). Jobfeed extension is currently disconnected after API restart; user asked to reload installed Jobfeed Jobright Source. Actual browser-source verification remains pending; do not claim sources fixed end-to-end yet.
- The original 13:26 Redis latency trigger and 13:27 Handshake abort cause cannot be reconstructed conclusively from historical logs. The available abort string does not prove user cancellation or the 30-second timer. Fixed demonstrable amplification/recovery defects; obtain current live request evidence before attributing any recurrent source failure.

## Additional isolation finding and final local state

- Reproduced another shared-failure path: `receive(batch)` rethrows a per-task persistence error into the shared WebSocket receiver, disconnecting all lanes. Isolated it to a task cancellation/failure. A bounded set of retired task IDs absorbs already-in-flight messages from completed/cancelled tasks; genuinely unknown task IDs still fail protocol validation. Regression verifies the healthy second source completes after the first fails and sends a late completion.
- Redis retry now also covers idempotent same-owner XCLAIM. Injected post-commit lost replies at enqueue, claim, and completion all recover with exactly one work callback and no pending stream work.
- Current verification: 46 Python tests, 47 extension tests, Ruff, mypy (three modules), DB quick_check pass. The mixed retry test verifies an old partial posting remains saved even when fresh discovery returns only a new posting.
- Isolated Redis probe: 1,200 steps, 64 slots, 1.181 seconds, max command 0.139 seconds, zero errors/pending. At 8 slots: 1.642 seconds, max command 0.027 seconds, zero errors/pending. Test namespace cleaned afterward. No evidence to change production concurrency or memory settings.
- Final backend activation PID 78138 uses the original environment. No scans active. Chrome extension remains disconnected; real post-fix Scan and browser confirmation are blocked pending Chrome recovery/reload. Native Chrome control timed out twice, browser extension navigation timed out, and browser discovery subsequently no longer exposed Chrome. A process sample was retained without changing/killing the user's browser.
- Completion status: code-level defects repaired and regression-verified; end-to-end recovery NOT yet verified. Original external abort/latency trigger remains uncertain. No commit, push, merge, or historical-status rewriting.

## Post-reload live verification (user reloaded extension)

- Extension connected; DB/Redis health ok. Retried diagnostic run as `59c7b974-a3d3-4ca6-af06-b19083c77a7c` at 2026-09-21 01:12:14 UTC (Sep 20 21:12 local).
- Actual source tabs opened and all lanes made fresh progress, rather than replaying cached errors. Handshake passed the old 25-row failure and completed 593 persisted rows. SpeedyApply completed 3,006 rows. LinkedIn retained prior partial work and completed 732 rows with an explicit uncertain-pagination warning.
- SpeedyApply reports 54/79 complete browser descriptions; LinkedIn found no recognizable cards at offset 350 after three attempts. These remain visible warnings, not failed sources or claims of complete coverage.

### Verified terminal result

Run `59c7b974-a3d3-4ca6-af06-b19083c77a7c` finished succeeded at 2026-09-21 01:15:43 UTC, with zero errors: 5,340 saved, 212 inserted, 5,128 updated. Per source: SpeedyApply 3,006; Jobright 1,009 (fresh results plus retained earlier partials); Handshake 593; LinkedIn 732 (fresh plus retained earlier partials). Runs UI independently displays Completed with warnings and the same total/new/updated counts. All four source lanes reached terminal completion; no source failed.

Redis journal for the resumed original root now has zero remaining keys and zero pending work; Redis uses 134.6 MiB. SQLite write receipts for generation 67 were reconciled against actual jobs rows: all 5,340 saved IDs exist. Evidence: `live-final.json` and `live-db-receipts.json`.

Remaining source-quality warnings are explicitly retained: SpeedyApply 54/79 complete browser descriptions; LinkedIn unconfirmed pagination at offset 350. These do not justify claiming complete upstream coverage. Scoped scan failure/recovery repair is now live and end-to-end verified; original transient external trigger remains unproven. No additional code changes, commit, push or merge in this continuation.

## Residual JD and pagination repair (authorized follow-up)

- [x] Reproduce JD failures from current database and actual browser pages.
- [x] Test-first fixes: generic full-JD fallback recognizes verified PathAI, Databricks, Duolingo, CDCN headings; Epic uses its verified raw-copy container and retains title validation. Greenhouse iframe fallback now uses bounded hydration polling rather than one immediate extraction.
- [x] Configuration repair: HTTP applytojob hosts cover HTTPS redirects; verified Orion and Dell destinations added. No general all-sites access. Direct comparison covers 17/19 formerly blocked latest targets.
- [x] IDEXX's two excluded redirects were verified as Workday maintenance, not job destinations. Explicit maintenance redirect is classified transient, without requesting access to the community site.
- [x] Captured a real signed-in LinkedIn offset-350 POST response (HTTP 200, 3,446,094 characters) containing h2 No results found and rendered guidance. Recognize this state while unknown zero-card responses still warn; cards take precedence.
- [x] Red evidence: jd-pagination-red.txt (4 regressions), jd-additional-red.txt (4 regressions), workday-red.txt (1 regression). Green: jd-pagination-green.txt, all 57 extension checks pass.
- [x] User reloaded extension after all production edits. Bridge connected.
- [x] Backed up 104 incomplete jobs' retry metadata to jd-retry-before.json and cleared only their cooldown timestamps. No job bodies, identities, or historical scan statuses rewritten.
- [ ] Fresh live run 47f79867-ed31-415d-b60b-5787b7985c66 reaches terminal state; reconcile JD recovery, remaining failures, LinkedIn end-state and UI with SQLite.

### First fresh run and discovered follow-up

Run 47f79867-ed31-415d-b60b-5787b7985c66 finished with one failed source (SpeedyApply). LinkedIn independently completed 634 saved rows without its pagination warning; Jobright completed 1,000, Handshake 593. JD lane had 103 browser targets but three worker tab IDs disappeared and were then repeatedly reused, producing 85 missing-tab errors across the audited prior cohort. The source of tab removal is unproven. Added a test-first bounded fresh-tab retry for the same target; a dead ID is removed from ownership and never cascades through later jobs. Full suite: 58 passed. Artifacts jd-tab-red.txt, jd-first-attempt-db.json and jd-live-first.json preserve failure evidence. User asked to reload extension after the now-terminal run; single-source SpeedyApply verification remains pending.

### Single-source verification and connection repair

- User reloaded tab-recovery patch. Started SpeedyApply-only run 9f41c0d7-0b77-4f10-8f29-1eb0a4c133b3. Backed up retry metadata again before clearing the same incomplete cohort. The 3,003 count is inventory reconciliation, NOT 3,003 JD network fetches: complete stored bodies are reused. Native phase reduced browser targets to 93.
- Run stalled at browser enrichment 0/93 while backend reported a connected extension; browser inventory showed no source worker tabs. Repeated socket acceptance was visible in backend logs. Original direct cause remains uncertain.
- Reproduced independent connection race: simultaneous startup/reconnect calls both pass the initial socket check, await storage, and create two sockets; stale socket messages can change connection state. Test fails pre-fix and passes after rechecking ownership after storage and ignoring stale open/message/error events. All 59 extension tests pass (connection-red.txt and jd-pagination-green.txt).
- Agent deliberately stopped this stalled diagnostic run (not user cancellation). Restarted idle backend with original process environment, current PID 83174. Requested reload because Chrome internal extension page cannot be controlled by the browser tool. Next verification should retry the retained failed run journal, reusing native results, rather than launching another fresh inventory scan.
- End-to-end JD repair remains unverified. Do not claim all source-quality defects fixed from passing unit tests or connected status alone.

### Runtime restoration during verification

- On backend restart, concurrently updated repost metadata exposed an existing SQLite upgrade-order defect: additive columns append after existing enrichment columns, but validation compared literal column order. Added test-first normalization of only known additive jobs definitions; all types/base DDL/constraints remain compared. 24 schema tests and targeted Ruff pass. Backed up current DB to before-repost-schema.sqlite before startup migration; 137,177 jobs before and after, three nullable repost columns installed.
- A concurrent malformed asyncpg import indentation also prevented imports; restored the missing indentation without changing the other task's feature implementation.
- Backend restored at PID 89729 using local config.toml. Extension reloaded by user after connection-race patch and connected.
- Retry 93986e22-173f-4249-991a-64160c26a46c failed before source processing because Redis/OrbStack were stopped. Started existing OrbStack container service; existing jobfeed-pipeline-redis-1 healthy, old journal still contains 5,790 keys.
- Current retry 005f5b6d-a823-4cb2-b59b-4e441bdb4a0f uses a new journal root (so do not claim that this runtime-error retry actually reused the older Redis root). Complete DB JDs are still reused by incremental source logic. Browser targets remain 93 and current partials are arriving. End-to-end validation remains pending.

## User correction: generic extraction, not site-specific patches

The user rejects maintaining per-company selectors/headings. Removed the newly added Jane Street production branch. Its captured HTML remains regression evidence; the test is intentionally red until a generic path satisfies it. Do not extend the host-specific patch approach.

Proposed boundary: preserve existing native ATS APIs and verified JSON-LD fast paths. For pages without structured JD, capture ordered visible text blocks plus headings and link evidence using generic DOM semantics. A generic extractor selects original block IDs; it must not generate JD text. Backend validates IDs, ordering, identity evidence, and explicit complete/partial/unavailable status before storing the exact source text. Never upgrade a partial or ambiguous page merely because it is long. Only missing/incomplete incrementally eligible jobs enter this fallback; complete stored descriptions remain reused. No new hashes or full-database reprocessing.

Decision pending: user asked whether model-assisted fallback is acceptable versus DOM-only fallback. Existing model configuration can be reused if selected. While waiting, retain current live scan and collect its terminal evidence; do not launch another broad retry or restart backend.

Acceptance: mixed unseen DOM layouts without hostname rules; alternate heading levels and wording; full paragraphs preserved; wrong-job, multi-job, unavailable, navigation-only and truncated inputs rejected; invalid model block selections rejected; stored complete jobs produce zero fallback calls; final actual-page and database evidence. Current live source repair and LinkedIn pagination results stay distinct from this new extraction redesign.

### Terminal evidence before generic redesign

005f5b6d-a823-4cb2-b59b-4e441bdb4a0f finished succeeded with warnings at 2026-09-21 02:24:14 UTC: 3,003 saved, 2 inserted, 3,001 updated, zero source errors. Browser returned 36/93 complete descriptions; this is not full JD repair. Final row quality: 2,913 full + 8 good = 2,921 complete; 65 missing + 16 partial + 1 stub = 82 incomplete. Source counters: 2,778 reused unique targets, 111 duplicate targets, 21 retry-deferred, zero native-enriched. Counters measure different stages and must not be summed as mutually exclusive row categories. Evidence jd-live-fourth-final.json. Generic extraction redesign remains pending and no claim of completion is warranted.

## Shared implementation accepted and implemented (2026-09-21)

User explicitly authorized implementation and asked to fix task 01a0bcb4-1665-7001-be96-83b4528fd36b as well. Read its actual local transcript after read_thread returned empty items: its remaining defect is LinkedIn repost extraction relying on heading layout. That task is idle. Work remains in this shared checkout; no delegated agents, new worktree, commit, push or merge.

- Shared job-page-snapshot.js captures ordered original blocks, structural ancestry and links, filters hidden/form/navigation content, and marks truncation. Removed company-specific main selectors and heading-word rules from the GitHub JD fallback; native APIs/JSON-LD remain fast paths.
- Shared JobPageExtractor uses the configured Stage A model (current codex-cli/gpt-5.6-luna), two concurrent calls per extractor, strict original-block selection validation, explicit identity status independent of JD completeness, and explicit target repost evidence. Provider failures are isolated per page. Snapshots are stored before interpretation; unchanged validated inputs reuse selections. Existing SQLite state and usage/cost APIs are reused; no new schema or hashes.
- Both SpeedyApply and LinkedIn extension source builders use the shared extractor. LinkedIn preserves its API JD and invokes interpretation only when additional repost evidence is present or JD is missing. Discovery transport now preserves repost fields. Removed eager per-listing posting-page fetch from cacheable discovery; complete jobs reuse their JD. Detail parsing runs in the extension isolated world to avoid the page's Trusted Types default policy stripping HTML.
- Real captured-page model verification: Jane Street 8594541002 returned all 1,992 original JD characters including compensation omitted by the discarded bespoke parser. LinkedIn 4188979310 header-only sample returned JD unavailable but affirmative explicit repost evidence. Current Chrome page independently shows that repost text. Synthetic recommendation-only repost and wrong-role variants both produced no false attribution or invented JD.
- Live DB verification through normal save_job: 393293 now full / 1,992 characters; 471501 retains full / 5,594 characters and now is_repost=True with original observed text. Backed up both complete records to shared-jobs-backup.json. discovered_at preserved. Actual DB refreshed state blocks Stage A dispatch with zero model-score calls. Receipt shared-db-verified.json. This is targeted DB verification, not proof of a fresh complete extension scan.
- Recorded six model validation calls: 115,801 input tokens, 1,613 output tokens, $0.01444532 adapter-estimated cost. The initial diagnostic call exposed using an external string as an internal usage FK; corrected to nullable internal ID. That initial call failed cost recording and is not included in the six recorded calls.
- Verification: 71 extension tests; 52 Python source/extraction/repost/CLI checks; Ruff; mypy two new modules; diff whitespace check pass. CLI fake needed existing state methods and its obsolete 4-disabled-sources assertion updated to actual 6. Test evidence under artifacts/scan-timeout-repair/shared-*.
- Activated backend PID 46809; health DB/Redis ok. User asked for one extension reload because new shared injection file and isolated-world execution need activation. Fresh small extension-to-model-to-DB run still pending that reload; do not claim full end-to-end rollout or all incomplete JDs repaired.

## Full retest after user reload — terminal evidence

Fresh all-source run e511fdf5-eff2-420a-b06a-3c5cc3ef32f4 ran 2026-09-21 03:02:02–03:13:52 UTC and succeeded with JD warnings, zero source errors. SpeedyApply 3,006; Jobright 1,000; LinkedIn 704; Handshake 593. Total 5,303 receipts, 71 inserted and 5,232 updated. API terminal record: shared-full-retest-final.json. Runs browser UI independently displays the same counters, all four completed sources, and the 35/60 JD warning. Restored stopped Vite frontend for this verification; backend remained active throughout.

Incremental proof: 60 actual browser targets, none previously full/good. Inventory 3,006 is not JD fetch count. 35/60 targets returned complete descriptions (33 original-block model selections plus two structured results). Source browser_enriched=36 counts affected rows, not unique targets. Current-source quality is 2,947 full + 9 good = 2,956/3,006; 50 incomplete rows remain. In the fixed before/after audit cohort, 32 formerly missing rows became full/good and zero complete rows degraded; cohort differs from fresh inventory, so do not conflate totals. Artifacts shared-full-retest-before.json, shared-full-retest-browser.json, shared-full-retest-db.json, shared-full-retest-selection-audit.json.

Remaining 25 targets include five page timeouts, two Workday maintenance responses, three partial selections, unavailable/mismatched jobs, invalid model selections rejected by validation, and incomplete snapshots. These remain actionable quality limitations; successful source completion is not a claim that every JD was repaired. LinkedIn completed without pagination warning after the apparent initial pause. Newly observed explicit repost text persisted through the full scan; evidence in shared-full-retest-receipts.json. Redis journal drained and automatically removed (zero remaining keys).

Current regression verification: 104 source/extraction/repost/CLI Python tests + 20 recovery/isolation tests passed; 71 extension tests passed. These are all relevant regression suites, not the entire repository suite. Outputs full-retest-python.txt, full-retest-recovery.txt, full-retest-extension.txt. No new production edits, commits, pushes or merges during this retest. No further extension reload needed for this tested implementation.

## Residual 50 — consolidated implementation

User authorized the agreed generic repairs. No company-specific selectors, new hashes, schema changes, commits or shipping.

- DOM snapshot now traverses FORM/role=form containers and excludes actual controls/labels. Red test reproduced lost job prose; green preserves paragraphs without input values.
- JD workers accept interactive documents while tracking/video resources remain loading, then perform bounded original-block readiness checks. Readable parent text is retained before following an application iframe; empty snapshots yield retryable page_timeout without model calls.
- Failed incomplete jobs predating extraction revision v3 receive one browser retry using existing state storage keyed by existing external identity/canonical ID. Attempts are recorded, subsequent scans honor cooldown; complete jobs remain reused. Authentication/rate-limit/maintenance cooldowns are preserved. This replaces the incomplete manually selected cooldown-reset cohort.
- Model prompt separates source completeness from employer writing quality and assesses paraphrased titles using URL/ID/employer/content evidence. Exact original block validation remains. Short, explicitly complete source text can be saved as complete; model errors or unavailable/partial pages remain visibly unsuccessful. Cache revision v3 avoids replaying old rejected selections.
- Test evidence: residual-red-js.txt (FORM and interactive wait failures), residual-red-python.txt (short original JD and retry revision failures); current residual-green-js.txt 75 passed; residual-python.txt 98 passed; scoped Ruff, mypy and diff whitespace checks pass. Added source integration verification for old failure -> one retry -> cooldown.
- Real saved-page model rerun: eight prior rejections returned original text (Jane Street, Game Plan Tech, three ByteDance, two Jobsbridge, JHU). Unrelated-hospital nurse negative returned unavailable. Evidence residual-model.log and residual-model-*.json. These are recorded-page checks, not a claim that current browser extraction repaired every record.
- Backend restarted after verifying no active runs; PID 12005. Requested one consolidated extension reload. Fresh live 50-row outcome and final database reconciliation pending user reload. No production job rows changed during this implementation; existing state stores new model selections and revision metadata only when attempted.

### Reload verification exposed document reuse contamination — NOT accepted

Run d39eedca-66b5-4b0e-9f73-62848c0e22b2 completed with 44 browser targets, 3,006 saved, no source error. Raw quality counters claimed 30 recovered, 20 incomplete. Body-level verification disproved accepting those counters: Varsity Brands job 444091 received JHU text, and a ByteDance target snapshot contained a SmartRecruiters URL. `tabs.update` could return while the previous job document was still interactive. Do not count these 30 as verified recovery.

Backed up the post-run rows to residual-before-rollback.json and restored ONLY eight JD/enrichment fields for the exact 50-row cohort from residual-live-before.json. Direct DB check confirms all 50 return to their pre-run incomplete state. Run history remains unchanged as diagnostic evidence; API success is not semantic correctness. No other job fields were restored.

Test-first document isolation repair: each target owns a fresh temporary tab; at most four concurrent tabs; tabs are removed after each result. This removes cross-job navigation reuse. Late redirects during readiness now reinject regardless of poll index, bounded by the existing readiness loop; previous code broke after poll 2 even for a later redirect. Cache and retry revision v4 prevent reuse of contaminated v3 selections and allow affected failures to retry once. Red evidence document-isolation-red.txt, residual-late-redirect-red.txt; current green 77 extension and 98 Python checks, Ruff and whitespace pass. Backend PID 22937, no active scan at restart. One further extension reload is required and was explained to user; no accepted live recovery claim until subsequent body/identity audit passes.

### Accepted document-isolation live verification after reload

User confirmed reload. Run 48478452-8dc7-4a34-8545-f135e423f693 completed with JD warnings, zero source errors, 3,006 saved (all updates), 44 unique browser targets. Verified all 3,006 durable write receipt IDs exist and Redis journal has zero remaining keys. First-run terminal record isolation-live-final.json and receipts isolation-live-receipts.json.

The exact original 50-row cohort now contains 36 FULL and 14 incomplete (9 missing, 4 partial, 1 stub). All 36 full bodies match their corresponding browser description or validated original-block selection byte-for-byte. Reviewed identity evidence and page URLs: Varsity pages now correctly remain unavailable on their recruiting homepage; no JHU text attribution; ByteDance snapshots point to the respective observed job IDs; four Waymo targets and both Textron jobs recovered. Evidence isolation-live-before.json, isolation-live-browser.json, isolation-live-db-audit.json. No accepted cross-job mixing found in this run.

Residual 14: Apple 2 + Akuna 1 explicitly unavailable; IDEXX 2 maintenance; Study.com 2 records missing host permission (one unique URL target); Precisely 1 + Varsity 2 pages without target JD; Old Mission 1 + Databricks 1 + Icon 1 wrapper/application/external-link content without complete extracted JD; Lyft 1 empty rendered snapshot. These are not all proven upstream impossibilities: wrapper traversal and dynamic rendering remain extraction limitations. Do not claim all JD defects fixed.

Lyft was opened in actual Chrome: initially blank, later full job prose rendered. A bounded second scan b647890e-3a83-4ea2-9443-4121ad7db6a4 cleared ONLY Lyft's retry timestamp (full backup lyft-retry-before.json). During this scan inventory changed: 3,004 rows, 2 inserted, 3,002 updated, 7 browser targets, 5 complete, zero source errors. The original 50-row outcome remains 36 recovered / 14 incomplete; Lyft still yielded empty snapshot. Source inventory updates explain why the run had more than one target; do not claim this was a literal one-target bridge run. lyft-retry-final.json preserves terminal evidence.

No additional production code edits or extension reload requested during this verification turn. Another evaluation run fd6c7154-9605-4a67-a200-d769fdbe96fd was active concurrently; not started, stopped, or otherwise changed by this verification. No backend restart while it runs. No commit/push/merge.

### Remaining 14: permissions, delayed hydration, observed ATS recovery

User asked whether permissions can repair the remaining failures and how to recover Lyft's empty dynamic page. Implemented exact study.com host permission (two DB records, one URL); manifest reload is still required. Plain HTTP currently returns 403, so permission activation alone is not proof of successful extraction.

Generic browser readiness now allows up to 120 half-second polls in the same fresh per-job document. Readable stable snapshots and complete native descriptions still exit early; cancellation and four-tab limit remain. Delayed hydration test fails under the former 30-poll limit (replayed old limit in artifact old-readiness.test.cjs; slow-readiness-red.txt), passes with new bound. This demonstrates timeout coverage, not live Lyft recovery.

Added _speedyapply_observed.py: derive Greenhouse board and exact job ID only from observed final URLs, iframe URLs, or ATS bootstrap script URLs. Verify exact returned job ID, reuse existing official API adapter, preserve browser/model fallback on failure. No company selector tables or new hashes. Script metadata is transmitted separately from frames and never navigated as a document. Official API recovery runs before semantic extraction and avoids model calls when original body is present. Initial red observed-ats-red.txt; response-ID mismatch and spoof-host negatives included.

Fresh HTTP through the actual new helper returned Databricks job 7586263002 (Associate Product Manager, New Grad (2027 Start), 5,030 text characters) and Old Mission 7796048003 (Software Engineer – 2027 Graduate Program (August Start), 2,501 characters). observed-ats-live.json preserves original bodies. These are verified source retrievals, NOT persisted recovery claims. No job rows changed this turn.

Icon's observed external careers URL redirects to https://icon.com/careers. It lists Founding Engineer but Apply links back to the original Ashby application ID; no role duties/requirements found in the fetched page. icon-careers-current.txt records visible text. Do not substitute generic company marketing for the JD. Precisely/Varsity homepage redirects still need same-job evidence for entry recovery; absence from the page alone is not conclusive closure. Workday maintenance remains transient with cooldown; permissions/model output cannot repair upstream downtime.

Current verification: remainder-green-python.txt 103 passed; remainder-green-js.txt 79 passed; scoped Ruff and mypy pass; git diff --check passes. Active runs endpoint was empty at check. Backend remains PID 22937, not restarted; manifest/worker activation and real DB cohort scan pending one consolidated reload. Accepted original 50 remains 36 FULL / 14 incomplete until a new live audit. No commits/pushes/merges. Complete records continue incremental reuse; next verification should back up and retry the affected incomplete cohort only, preserving maintenance cooldowns.

### Latest LinkedIn disconnect diagnosis (2026-09-21)

Investigated run e12edbd2-2886-4824-ba9a-735585da5f8e. LinkedIn 472 saved / Jobright 880 saved both failed due shared extension disconnect; all 4,921 run write-receipt IDs exist. Reconnection was logged shortly afterward, but current code immediately fails pending tasks and cancels extension work, with no resume/reissue. Reproduced this with the current bridge in memory; partial batches survive and reconnected queue is empty. Initial disconnect trigger cannot be established: route swallows WebSocket/protocol exceptions and extension discards close reason. No evidence of user cancellation; later Jobright HTTP 429 does not explain earlier LinkedIn failure. Evidence and repair/validation scope: artifacts/linkedin-disconnect-20260921/diagnosis.md. Production changes and live retry not performed in this diagnosis.
