# Redis scan journal memory repair

Scope: release redundant browser partials only after durable step completion; remove drained, DB-finalized journals immediately rather than retain full JDs for seven days. Preserve all unfinished journals and keep run mappings briefly for diagnostics. Do not change memory limits, eviction policy, job identity, or unrelated edits.

Acceptance: failing then passing Redis integration tests for partial cleanup, replay, finalized cleanup and unfinished retention; existing commit-before-ACK and browser restart coverage; clean up only DB-verified drained local journals; restart idle local API with existing configuration, Retry the failed maxmemory scan, and read back terminal run state plus Redis memory. No push/merge.

Risks: deleting uncommitted work or losing replay after partial cleanup. Completion must persist the result before deleting partials. Final cleanup requires both terminal DB state and drained marker. An unrelated source failure must be reported separately from memory recovery.

Progress: diagnosis confirmed 502 MiB used against 512 MiB, with retained complete journals and redundant browser partials dominant. Implementation and verification pending.

## Verification and local rollout

- Red: real Redis tests failed in three expected assertions (completed journals retained; browser partials retained; drained failed run retained), with nine passing.
- Green: 18 tests passed across Redis integration, SQLite batch receipts, parallel scan lanes and bridge lanes. Ruff check and format check passed. Cached legacy results also release redundant partials on replay.
- Review: result SET precedes partial UNLINK inside the fenced Lua completion; incomplete callbacks retain partials. Full journal cleanup requires persisted terminal state, DB drained marker and zero stream length. Retry of drained runs already starts a new root, while undrained retries retain their old root.
- Local cleanup: backed up 22,526 keys to artifacts/redis-memory-repair/finalized-journals.jsonl; released five DB-verified finalized/drained roots; Redis fell from ~502 MiB to 171.73 MiB. Three unfinished roots retained. SQLite quick_check returned ok.
- Idle API restarted with its existing process environment; health reports DB and Redis ok; browser extension reconnected.
- Clicked Retry in the user's Chrome Runs page for cf4046d7-b3cc-43f2-8bf4-575c822040d3. New run 514588b1-4fdf-4430-acc4-275279a49f67 resumes that journal; Handshake successfully saved 598 jobs past the original failing stage. Terminal result pending.
- Existing unrelated working-tree changes left intact. No commit, push, merge, memory-limit change, eviction-policy change, or new identity fields.

## 2026-09-25 terminal journal retention follow-up

- Result: failed or stopped scans without a drained marker retain Redis journals forever. Ten terminal roots currently hold about 54,735 keys and exhaust the 512 MiB Redis cap. No scan is active.
- Scope: give undrained terminal journals a bounded retry window; preserve data during that window and remove the expiry when a retry takes ownership. Keep immediate deletion for drained, terminal journals. Clear only this project's Redis DB 0 journal and run-mapping keys after recording the affected run IDs and their DB state; leave DB 15 and other data untouched. Explicit cache clearing gives up replay of those old runs, so their Redis-run DB markers must be removed so Retry starts fresh.
- Verification: first record a failing Redis integration check for retention and resume, then passing results; direct readback of DB 0 key count and Redis memory; check SQLite state and active runs. No push or merge.
- Risks: delayed Retry cannot replay after the retention window or explicit cache clearing. Do not delete a running journal or change the Redis eviction policy.
- Red: focused Redis tests initially failed in three expected assertions because undrained terminal keys had no TTL.
- Green: 18 Redis integration tests passed. Ruff check and format check passed on the three changed Python files.
- Local clear: verified no active runs and all ten journal roots terminal; recorded 54,735 journal keys, 46 run mappings and matching SQLite state in `artifacts/redis-cache-clear-20260925/before.json`. Removed only those DB 0 keys and 46 stale Redis-run state rows. DB 0 now has zero keys; SQLite quick_check is ok. DB 15 was not cleared and remains available to the test suite.
- Live service: `/api/health` reports DB and Redis ok; `/api/runs/active` is empty. Redis used memory settled near 2.45 MiB after asynchronous unlink. A new full scan was not started.

## Final result

Retry 514588b1-4fdf-4430-acc4-275279a49f67 finished succeeded at 2026-09-20 02:34:29 UTC (about four minutes): 5,264 jobs, 501 inserted, 4,763 updated, zero recorded errors. Chrome Runs page independently displays Succeeded and those counts. Redis dropped automatically to about 134.7 MiB after finalization; the retried root has zero remaining journal keys. DB state confirms terminal success and drained marker; quick_check remains ok. Readback and sampled memory observations are in artifacts/redis-memory-repair/.

Non-blocking source limitations: SpeedyApply reports 21/90 complete browser-fetched descriptions; LinkedIn stopped after no recognizable cards at offset 700 across three attempts (674 results). These were source warnings, not Redis failures; no claim that all possible source results were fetched. Two older unfinished roots remain recoverable rather than being purged. All scoped implementation and Retry verification tasks complete.
