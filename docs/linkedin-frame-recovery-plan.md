# LinkedIn frame recovery

Scope: recover only `Frame with ID 0 was removed` in the production LinkedIn
worker, whether during injection or asynchronous batch execution. No full scan
restart, tab recreation, HTTP/authentication retries, or other source recovery.

- [x] Red: four recovery tests failed against the original worker (no recovery,
  one attempt rather than three, cancellation and closed-tab checks not reached).
- [x] Retry a failed batch at its unchanged offset, at most twice, with a 1000 ms
  delay, page-completion wait, tab existence/job-page check and script reinjection.
- [x] Preserve prior batches and existing job-ID deduplication. Stop on cancellation,
  tab closure, login/off-site navigation, unrelated errors and exhausted retries.
- [x] Add scoped console events for navigation/loading/discarding, external tab
  removal and retry, containing task/tab IDs, offset and timestamp, not URL query
  strings or authentication headers. Remove listeners before normal cleanup.
- [x] Verification: 28 extension tests passed; pilot-worker.js syntax check passed.
- [ ] Live browser verification after the active scan finishes and the user reloads
  the extension. Do not reload during the current scan. No claim of live recovery.

Review: injected frame-loss tests verify recovery control flow, not the original
browser event's cause. Lifecycle events are diagnostic console output, not durable
server logs. A lost in-flight batch is fetched again; only returned batches count.
No production data changes, extension reload, commit, push or merge performed.
