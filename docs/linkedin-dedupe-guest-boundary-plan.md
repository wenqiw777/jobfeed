# LinkedIn dedupe without authenticated browser activity

## Scope and acceptance

Use observed LinkedIn job IDs and saved source facts before any request. Missing
information may use anonymous Guest details. Dedupe must never open LinkedIn in
Chrome, including redirects and embedded requests. Uncertain identities remain
unresolved. Existing cross-source identity rules still decide merges. No hash
fields, calculations, checks, or dependencies are added.

Implementation is authorized; push, merge, Mini deployment and extension reload
are not part of this request. Work is on codex/linkedin-guest-dedupe, based on the
Mini source branch origin/codex/application-route-dedup. Pre-existing local edits
were retained. Two colliding untracked files were preserved under
artifacts/linkedin-guest-dedupe/pre-switch before switching branches.

## Changes

- [x] Resolve LinkedIn entry URLs and redirect targets using native-ID data lookup.
  Existing records or substantive source JD require no LinkedIn page request.
- [x] Missing evidence uses a fresh anonymous Guest client and the existing Apply
  link parser. Guest failures never cause browser fallback.
- [x] Discovery, enrichment and resolution use one process/event-loop Guest queue:
  serialized requests, at least one second between starts, duplicate detail reuse
  for one hour (512 entries), and a five-minute block cooldown. Search results
  are not cached. Existing durable route receipts and saved jobs remain reused.
- [x] Browser adapter and extension reject LinkedIn targets. Tab-scoped network
  rules also block LinkedIn redirects, frames and subresources.
- [x] Preserve conservative ATS verification; no forced identity merges or source
  JD overwrites.

## Evidence and findings

- Before implementation, all five Python boundary tests failed: LinkedIn page
  requests occurred even with existing data and the browser adapter dispatched
  LinkedIn targets. After implementation all five passed.
- Extension rejection test failed before its guard (no rejection; 30-second
  page timeout) and passed after the guard.
- Initial related regression run: 245 tests passed, including actual SQLite
  native-ID association without network access.
- Real isolated Chromium verification initially found that a DNR rule omitting
  resourceTypes excludes main_frame. The existing generic rule still blocked
  that navigation. Fixed the dedicated LinkedIn rule with explicit resource
  types and added a regression assertion; final evidence is recorded below.

## Limits

Guest can still be unavailable. No live LinkedIn access is needed for verification
and none is authorized as part of this test. The shared queue/cache is per server
process, not cross-process; separate CLI invocations do not share it. No UI change.
Mini will retain its current behavior until an explicitly authorized deployment
and extension reload. The existing model timeout repair is outside this change.

## Final verification

- 247 related Python tests passed (14 dedicated boundary/Guest checks).
- 11 extension tests passed.
- Ruff check and formatting passed for changed Python files; mypy passed all 376
  source files. `git diff --check` passed.
- An isolated Chromium profile loaded the real extension worker. LinkedIn target
  rejection left tab count unchanged (1 to 1); Chrome's network-rule matcher
  confirmed the dedicated block for main_frame, sub_frame and xmlhttprequest.
  No LinkedIn network requests occurred. Evidence:
  `artifacts/linkedin-guest-dedupe/browser-proof.json`; reproducible driver:
  `artifacts/linkedin-guest-dedupe/browser_check.py`.
- No remaining release-blocking findings from the scoped review. Mini activation
  remains explicitly outside this code-only delivery.
